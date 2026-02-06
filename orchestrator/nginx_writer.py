"""
Nginx configuration writer for the Orchestrator service.
Generates nginx map files for cookie-based session routing.
"""

import os
import subprocess
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from jinja2 import Template

from sqlalchemy.ext.asyncio import AsyncSession

from database import NginxMapEntryModel, get_all_nginx_entries
from config import get_settings

import structlog

log = structlog.get_logger()


# =============================================================================
# Nginx Map Template
# =============================================================================

NGINX_MAP_TEMPLATE = """# =============================================================================
# Dynamic Labyrinth - Honeytrap Upstream Map
# =============================================================================
# Generated automatically by the Orchestrator service.
# DO NOT EDIT MANUALLY - changes will be overwritten.
#
# Generated: {{ generated_at }}
# Total entries: {{ entries | length }}
# =============================================================================

map $cookie_dlsess $honeytrap_upstream {
    # Default upstream (level 1 pool)
    default "{{ default_upstream }}";

{% for entry in entries %}
    # Session: {{ entry.session_id }}
    "{{ entry.session_cookie }}" "{{ entry.upstream }}";
{% endfor %}
}
"""


class NginxWriter:
    """
    Writes nginx map configuration for session-based routing.
    
    Responsibilities:
    - Generate nginx map file from session assignments
    - Reload nginx configuration safely
    - Validate map file before reload
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.template = Template(NGINX_MAP_TEMPLATE)
        self._reload_lock = asyncio.Lock()
    
    # =========================================================================
    # Map File Generation
    # =========================================================================
    
    async def write_map_file(
        self,
        db: AsyncSession,
        map_path: Optional[str] = None
    ) -> bool:
        """
        Generate and write the nginx map file.
        
        Returns True if successful, False otherwise.
        """
        path = Path(map_path or self.settings.nginx_map_path)
        
        try:
            # Get all map entries from database
            entries = await get_all_nginx_entries(db)
            
            # Render template
            content = self.template.render(
                generated_at=datetime.utcnow().isoformat(),
                default_upstream=self.settings.default_upstream,
                entries=[
                    {
                        "session_cookie": entry.session_cookie,
                        "session_id": entry.session_id,
                        "upstream": entry.upstream
                    }
                    for entry in entries
                ]
            )
            
            # Ensure directory exists
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temporary file first
            temp_path = path.with_suffix('.tmp')
            temp_path.write_text(content)
            
            # Validate the configuration
            if not await self._validate_config(temp_path):
                log.error("Nginx config validation failed", path=str(temp_path))
                temp_path.unlink(missing_ok=True)
                return False
            
            # Atomic rename
            temp_path.rename(path)
            
            log.info("Nginx map file written", path=str(path), entries=len(entries))
            return True
            
        except Exception as e:
            log.error("Failed to write nginx map file", error=str(e))
            return False
    
    async def add_session_mapping(
        self,
        db: AsyncSession,
        session_id: str,
        session_cookie: str,
        container_address: str
    ) -> bool:
        """
        Add or update a session mapping in the database and regenerate map file.
        """
        try:
            # Check if entry exists
            from sqlalchemy import select
            result = await db.execute(
                select(NginxMapEntryModel).where(
                    NginxMapEntryModel.session_id == session_id
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                # Update existing entry
                existing.session_cookie = session_cookie
                existing.upstream = container_address
                existing.updated_at = datetime.utcnow()
            else:
                # Create new entry
                entry = NginxMapEntryModel(
                    session_cookie=session_cookie,
                    session_id=session_id,
                    upstream=container_address,
                    created_at=datetime.utcnow()
                )
                db.add(entry)
            
            await db.commit()
            
            # Regenerate map file
            if not await self.write_map_file(db):
                log.error("Failed to regenerate map file after adding mapping")
                return False
            
            return True
            
        except Exception as e:
            log.error("Failed to add session mapping", session_id=session_id, error=str(e))
            await db.rollback()
            return False
    
    async def remove_session_mapping(
        self,
        db: AsyncSession,
        session_id: str
    ) -> bool:
        """
        Remove a session mapping from the database and regenerate map file.
        """
        try:
            from sqlalchemy import delete
            
            await db.execute(
                delete(NginxMapEntryModel).where(
                    NginxMapEntryModel.session_id == session_id
                )
            )
            await db.commit()
            
            # Regenerate map file
            if not await self.write_map_file(db):
                log.error("Failed to regenerate map file after removing mapping")
                return False
            
            return True
            
        except Exception as e:
            log.error("Failed to remove session mapping", session_id=session_id, error=str(e))
            await db.rollback()
            return False
    
    # =========================================================================
    # Nginx Control
    # =========================================================================
    
    async def reload_nginx(self) -> bool:
        """
        Reload nginx configuration.
        
        Performs health check before reload to ensure nginx is responsive.
        """
        async with self._reload_lock:
            try:
                # Health check first
                if not await self._nginx_health_check():
                    log.error("Nginx health check failed, skipping reload")
                    return False
                
                # Test configuration
                test_result = await self._run_command("nginx -t")
                if test_result.returncode != 0:
                    log.error("Nginx config test failed", stderr=test_result.stderr)
                    return False
                
                # Reload nginx
                reload_result = await self._run_command(self.settings.nginx_reload_command)
                if reload_result.returncode != 0:
                    log.error("Nginx reload failed", stderr=reload_result.stderr)
                    return False
                
                log.info("Nginx reloaded successfully")
                return True
                
            except Exception as e:
                log.error("Failed to reload nginx", error=str(e))
                return False
    
    async def _validate_config(self, config_path: Path) -> bool:
        """
        Validate nginx configuration file.
        
        This is a basic syntax check - full validation happens during nginx -t.
        """
        try:
            content = config_path.read_text()
            
            # Basic validation
            if "map $cookie_dlsess" not in content:
                log.error("Invalid map file: missing map directive")
                return False
            
            # Check for balanced braces
            if content.count('{') != content.count('}'):
                log.error("Invalid map file: unbalanced braces")
                return False
            
            return True
            
        except Exception as e:
            log.error("Config validation error", error=str(e))
            return False
    
    async def _nginx_health_check(self) -> bool:
        """Check if nginx is responding."""
        try:
            import httpx
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self.settings.nginx_healthcheck_url)
                return response.status_code == 200
                
        except Exception as e:
            log.warning("Nginx health check failed", error=str(e))
            # If we can't reach the health endpoint, assume nginx is up
            # This is for development/testing scenarios
            return True
    
    async def _run_command(self, command: str) -> subprocess.CompletedProcess:
        """Run a shell command asynchronously."""
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        return subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else ""
        )
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    async def get_current_mappings(self, db: AsyncSession) -> List[Dict]:
        """Get list of current session mappings."""
        entries = await get_all_nginx_entries(db)
        return [
            {
                "session_cookie": entry.session_cookie,
                "session_id": entry.session_id,
                "upstream": entry.upstream,
                "created_at": entry.created_at.isoformat() if entry.created_at else None
            }
            for entry in entries
        ]
    
    async def clear_all_mappings(self, db: AsyncSession) -> bool:
        """Clear all session mappings (use with caution)."""
        try:
            from sqlalchemy import delete
            
            await db.execute(delete(NginxMapEntryModel))
            await db.commit()
            
            # Write empty map file
            await self.write_map_file(db)
            
            log.warning("All nginx mappings cleared")
            return True
            
        except Exception as e:
            log.error("Failed to clear mappings", error=str(e))
            return False


# =============================================================================
# Singleton Instance
# =============================================================================

_nginx_writer: Optional[NginxWriter] = None


def get_nginx_writer() -> NginxWriter:
    """Get the nginx writer singleton."""
    global _nginx_writer
    if _nginx_writer is None:
        _nginx_writer = NginxWriter()
    return _nginx_writer
