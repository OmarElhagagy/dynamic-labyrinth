"""
Event normalization module - Converts Honeytrap events to Cerebrum format
"""

import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
import logging

from pydantic import ValidationError

from models.events import NormalizedEvent

logger = logging.getLogger(__name__)

def normalize_event(honeytrap_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize raw Honeytrap event to Cerebrum format
    
    Args:
        honeytrap_event: Raw event from Honeytrap
        
    Returns:
        Normalized event dict or None if failed
    """
    try:
        # Defensive parsing
        if not isinstance(honeytrap_event, dict):
            logger.warning(f"Invalid event type: {type(honeytrap_event)}")
            return None
        
        # Extract and map fields
        mapped_data = map_honeytrap_fields(honeytrap_event)
        
        if not mapped_data:
            return None
        
        # Validate using Pydantic model
        normalized_event = NormalizedEvent(**mapped_data)
        
        logger.debug(f"Normalized event: {normalized_event.id}")
        return normalized_event.dict()
        
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected normalization error: {e}")
        return None

def map_honeytrap_fields(raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map Honeytrap fields to normalized schema"""
    try:
        # Generate session ID if missing
        session_id = extract_session_id(raw_event)
        if not session_id:
            logger.warning("Could not extract session ID")
            return None
        
        # Map event type
        event_type = map_event_type(raw_event)
        
        # Extract indicators
        indicators = extract_indicators(raw_event)
        
        # Get source IP
        source_ip = raw_event.get('remote_ip') or raw_event.get('source_ip') or '0.0.0.0'
        
        mapped_data = {
            "id": raw_event.get('event_id') or f"evt-{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
            "timestamp": raw_event.get('timestamp') or datetime.utcnow().isoformat() + "Z",
            "protocol": raw_event.get('service', 'unknown'),
            "event_type": event_type,
            "indicators": indicators,
            "source_ip": source_ip
        }
        
        return mapped_data
        
    except Exception as e:
        logger.error(f"Error mapping fields: {e}")
        return None

def extract_session_id(event: Dict[str, Any]) -> Optional[str]:
    """Extract or generate session ID"""
    try:
        # Try various session ID fields
        session_id = (
            event.get('session_id') or
            event.get('connection_id') or
            event.get('channel_id') or
            generate_session_id(event)
        )
        
        if not session_id or session_id == 'unknown':
            return None
            
        return str(session_id)
        
    except Exception as e:
        logger.error(f"Error extracting session ID: {e}")
        return None

def generate_session_id(event: Dict[str, Any]) -> str:
    """Generate session ID from available data"""
    source_ip = event.get('remote_ip') or event.get('source_ip') or 'unknown'
    service = event.get('service', 'unknown')
    timestamp = event.get('timestamp', '')
    
    # Create a hash-based session ID
    import hashlib
    session_data = f"{source_ip}:{service}:{timestamp}"
    session_hash = hashlib.md5(session_data.encode()).hexdigest()[:12]
    
    return f"session_{session_hash}"

def map_event_type(event: Dict[str, Any]) -> str:
    """Map Honeytrap event type to standardized types"""
    event_type = event.get('type', '').lower()
    data = event.get('data', {})
    
    # SSH events
    if 'ssh' in event_type:
        if 'auth' in event_type or 'login' in event_type:
            if data.get('success'):
                return 'authentication_success'
            else:
                return 'authentication_failed'
        elif 'connection' in event_type:
            return 'connection_established'
        elif 'command' in event_type:
            return 'command_executed'
    
    # HTTP events
    elif 'http' in event_type:
        if 'request' in event_type:
            return 'http_request'
        elif 'response' in event_type:
            return 'http_response'
    
    # TCP events
    elif 'tcp' in event_type:
        if 'connection' in event_type:
            return 'connection_established'
        elif 'data' in event_type:
            return 'data_transfer'
    
    # Generic events
    elif 'login' in event_type:
        return 'authentication_attempt'
    elif 'scan' in event_type or 'probe' in event_type:
        return 'scan_detected'
    
    return 'unknown'

def extract_indicators(event: Dict[str, Any]) -> List[str]:
    """Extract security indicators from event data"""
    indicators = []
    data = event.get('data', {})
    
    # Username indicators
    username = data.get('username')
    if username and is_suspicious_username(username):
        indicators.append(f"suspicious_username:{username}")
    
    # Password indicators
    password = data.get('password')
    if password and is_weak_password(password):
        indicators.append("weak_password_attempt")
    
    # HTTP path indicators
    path = data.get('path') or data.get('uri')
    if path and is_suspicious_path(path):
        indicators.append(f"suspicious_path:{path}")
    
    # User agent indicators
    user_agent = data.get('user_agent') or data.get('user-agent')
    if user_agent and is_suspicious_user_agent(user_agent):
        indicators.append("suspicious_user_agent")
    
    # Command indicators
    command = data.get('command')
    if command and is_suspicious_command(command):
        indicators.append(f"suspicious_command:{command}")
    
    # Port scanning indicators
    if event.get('type', '').lower() in ['port_scan', 'tcp_scan']:
        indicators.append("port_scanning")
    
    # Add custom indicators from event
    custom_indicators = event.get('indicators', [])
    if isinstance(custom_indicators, list):
        indicators.extend([str(ind) for ind in custom_indicators if ind])
    
    return list(set(indicators))  # Remove duplicates

def is_suspicious_username(username: str) -> bool:
    """Check if username is suspicious"""
    suspicious_usernames = [
        'root', 'admin', 'test', 'guest', 'user', 'oracle', 'mysql',
        'postgres', 'ftp', 'anonymous', 'backup', 'ubuntu', 'centos'
    ]
    return username.lower() in suspicious_usernames

def is_weak_password(password: str) -> bool:
    """Check if password is weak/common"""
    weak_passwords = [
        'password', '123456', 'admin', 'test', 'root', '1234',
        'pass', 'password123', 'letmein', 'welcome'
    ]
    return password.lower() in weak_passwords or len(password) < 4

def is_suspicious_path(path: str) -> bool:
    """Check if HTTP path is suspicious"""
    suspicious_paths = [
        '/admin', '/shell', '/cmd', '/exec', '/wp-admin', '/phpmyadmin',
        '/.env', '/config', '/backup', '/cgi-bin', '/bin', '/etc'
    ]
    path_lower = path.lower()
    return any(susp in path_lower for susp in suspicious_paths)

def is_suspicious_user_agent(user_agent: str) -> bool:
    """Check if User-Agent is suspicious"""
    suspicious_agents = [
        'nikto', 'sqlmap', 'nmap', 'metasploit', 'acunetix',
        'nessus', 'wpscan', 'burpsuite', 'zap'
    ]
    ua_lower = user_agent.lower()
    return any(agent in ua_lower for agent in suspicious_agents)

def is_suspicious_command(command: str) -> bool:
    """Check if command is suspicious"""
    suspicious_commands = [
        'rm ', 'del ', 'format', 'shutdown', 'reboot', 'passwd',
        'chmod 777', 'wget', 'curl', 'nc ', 'netcat', 'python -c',
        'perl -e', 'bash -i', '/bin/bash'
    ]
    cmd_lower = command.lower()
    return any(susp in cmd_lower for susp in suspicious_commands)