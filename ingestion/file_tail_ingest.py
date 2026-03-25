"""
File tail ingestion for Honeytrap file pushers
"""

import asyncio
import json
import logging
import aiofiles
import os
from pathlib import Path
from typing import Optional

from normalize import normalize_event
from queues.redis_queue import RedisQueue

logger = logging.getLogger(__name__)

class FileTailIngestor:
    """Monitor and process Honeytrap JSONL files"""
    
    def __init__(self, queue: RedisQueue, watch_dir: str = "/var/log/honeytrap"):
        self.queue = queue
        self.watch_dir = Path(watch_dir)
        self.watched_files = {}
        self.running = False
        self.task = None
        
    async def start(self):
        """Start file monitoring"""
        if self.running:
            logger.warning("FileTailIngestor already running")
            return
            
        self.running = True
        logger.info(f"Starting file tail ingestor for {self.watch_dir}")
        
        # Create watch directory if it doesn't exist
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        
        self.task = asyncio.create_task(self._monitor_loop())
        
    async def stop(self):
        """Stop file monitoring"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("File tail ingestor stopped")
    
    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                await self._scan_files()
                await asyncio.sleep(5)  # Check every 5 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(10)  # Wait longer on error
    
    async def _scan_files(self):
        """Scan directory for JSONL files and process them"""
        try:
            if not self.watch_dir.exists():
                logger.warning(f"Watch directory {self.watch_dir} does not exist")
                return
            
            # Find all JSONL files
            for file_path in self.watch_dir.glob("*.jsonl"):
                if file_path.is_file():
                    await self._process_file(file_path)
                    
        except Exception as e:
            logger.error(f"Error scanning directory: {e}")
    
    async def _process_file(self, file_path: Path):
        """Process a single JSONL file"""
        try:
            current_size = file_path.stat().st_size
            last_position = self.watched_files.get(file_path, 0)
            
            # Handle file rotation or truncation
            if current_size < last_position:
                last_position = 0
            
            # Read new data
            if current_size > last_position:
                async with aiofiles.open(file_path, 'r') as file:
                    await file.seek(last_position)
                    new_content = await file.read()
                    
                    if new_content:
                        processed = await self._process_content(new_content, str(file_path))
                        if processed > 0:
                            logger.debug(f"Processed {processed} events from {file_path.name}")
                
                # Update file position
                self.watched_files[file_path] = current_size
                
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")
    
    async def _process_content(self, content: str, source: str) -> int:
        """Process file content line by line"""
        processed_count = 0
        
        for line_num, line in enumerate(content.strip().split('\n'), 1):
            if not line.strip():
                continue
                
            try:
                event_data = json.loads(line)
                event_data['source_file'] = source
                event_data['source_line'] = line_num
                
                normalized_event = normalize_event(event_data)
                
                if normalized_event:
                    await self.queue.publish("honeytrap_events", normalized_event)
                    processed_count += 1
                else:
                    logger.warning(f"Failed to normalize event from {source}:{line_num}")
                    
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in {source}:{line_num} - {e}")
            except Exception as e:
                logger.error(f"Error processing line {source}:{line_num} - {e}")
        
        return processed_count

# Singleton instance
file_ingestor = FileTailIngestor(RedisQueue())

async def start_file_ingestion():
    """Start file ingestion service"""
    await file_ingestor.start()

async def stop_file_ingestion():
    """Stop file ingestion service"""
    await file_ingestor.stop()