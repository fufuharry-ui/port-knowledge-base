import os
from datetime import datetime
from pathlib import Path

class ActivityLogger:
    def __init__(self, base_dir="wiki"):
        self.base_dir = Path(base_dir)
        self.log_file = self.base_dir / "log.md"
        
        # Ensure wiki directory exists
        if not self.base_dir.exists():
            self.base_dir.mkdir(parents=True, exist_ok=True)
            
    def log(self, action: str, target: str, details: str = "") -> None:
        """
        Logs an action in append-only mode.
        Format: ## [YYYY-MM-DD HH:MM:SS] {action} | {target}
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"## [{now}] {action} | {target}\n"
        if details:
            entry += f"{details}\n"
        entry += "\n"
        
        # Append to log file
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(entry)

# Global singleton logger
global_logger = ActivityLogger()