"""
Finance Tracker — Recurring Expenses
Track subscriptions and bills that repeat
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from .storage import get_storage
    from .categories import get_emoji, detect_category
except ImportError:
    from storage import get_storage
    from categories import get_emoji, detect_category


class RecurringManager:
    """Manage recurring expenses like subscriptions and bills."""
    
    FREQUENCIES = {
        "daily": 1,
        "weekly": 7,
        "biweekly": 14,
        "monthly": 30,
        "quarterly": 90,
        "yearly": 365
    }
    
    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path.home() / ".finance-tracker"
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.recurring_file = self.data_dir / "recurring.json"
        self._init_file()
    
    def _init_file(self):
        if not self.recurring_file.exists():
            self._save({
                "recurring": [],
                "created_at": datetime.now().isoformat()
            })
    
    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.recurring_file) as f:
                return json.load(f)
        except:
            return {"recurring": []}
    
    def _save(self, data: Dict[str, Any]):
        with open(self.recurring_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def add_recurring(
        self,
        amount: int,
        description: str,
        frequency: str = "monthly",
        day: Optional[int] = None,
        category: Optional[str] = None,
        start_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add a recurring expense.
        
        Args:
            amount: Amount in currency units
            description: Description of the expense
            frequency: daily, weekly, biweekly, monthly, quarterly, yearly
            day: Day of month (1-31) for monthly, or day of week (0-6) for weekly
            category: Category (auto-detected if not provided)
            start_date: When this subscription started (ISO format)
        """
        data = self._load()
        
        if category is None:
            category = detect_category(description)
        
        if day is None:
            day = datetime.now().day if frequency == "monthly" else datetime.now().weekday()
        
        recurring = {
            "id": len(data["recurring"]) + 1,
            "amount": amount,
            "description": description,
            "category": category,
            "frequency": frequency,
            "day": day,
            "start_date": start_date or datetime.now().isoformat(),
            "last_logged": None,
            "next_due": self._calculate_next_due(frequency, day),
            "active": True,
            "created_at": datetime.now().isoformat()
        }
        
        data["recurring"].append(recurring)
        self._save(data)
        return recurring
    
    def _calculate_next_due(self, frequency: str, day: int) -> str:
        """Calculate the next due date."""
        now = datetime.now()
        
        if frequency == "daily":
            next_due = now + timedelta(days=1)
        elif frequency == "weekly":
            days_ahead = day - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_due = now + timedelta(days=days_ahead)
        elif frequency == "biweekly":
            days_ahead = day - now.weekday()
            if days_ahead <= 0:
                days_ahead += 14
            next_due = now + timedelta(days=days_ahead)
        elif frequency == "monthly":
            if now.day >= day:
                # Next month
                if now.month == 12:
                    next_due = now.replace(year=now.year + 1, month=1, day=min(day, 28))
                else:
                    next_due = now.replace(month=now.month + 1, day=min(day, 28))
            else:
                next_due = now.replace(day=min(day, 28))
        elif frequency == "quarterly":
            next_due = now + timedelta(days=90)
        elif frequency == "yearly":
            next_due = now.replace(year=now.year + 1)
        else:
            next_due = now + timedelta(days=30)
        
        return next_due.isoformat()
    
    def remove_recurring(self, id_or_name: str) -> bool:
        """Remove or deactivate a recurring expense."""
        data = self._load()
        
        for item in data["recurring"]:
            if str(item["id"]) == str(id_or_name) or item["description"].lower() == id_or_name.lower():
                item["active"] = False
                self._save(data)
                return True
        
        return False
    
    def get_recurring(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all recurring expenses."""
        data = self._load()
        items = data["recurring"]
        
        if active_only:
            items = [r for r in items if r.get("active", True)]
        
        return sorted(items, key=lambda x: x.get("next_due", ""))
    
    def get_due_today(self) -> List[Dict[str, Any]]:
        """Get recurring expenses due today."""
        today = datetime.now().date()
        due = []
        
        for item in self.get_recurring():
            next_due = datetime.fromisoformat(item["next_due"]).date()
            if next_due <= today:
                # Check if already logged today
                if item["last_logged"]:
                    last_logged = datetime.fromisoformat(item["last_logged"]).date()
                    if last_logged == today:
                        continue
                due.append(item)
        
        return due
    
    def log_recurring(self, recurring_id: int) -> Dict[str, Any]:
        """Log a recurring expense as a transaction."""
        data = self._load()
        storage = get_storage()
        
        for item in data["recurring"]:
            if item["id"] == recurring_id:
                # Add to transactions
                tx = storage.add_transaction(
                    item["amount"],
                    f"[Recurring] {item['description']}",
                    item["category"]
                )
                
                # Update recurring record
                item["last_logged"] = datetime.now().isoformat()
                item["next_due"] = self._calculate_next_due(item["frequency"], item["day"])
                self._save(data)
                
                return tx
        
        return None
    
    def process_due(self) -> List[Dict[str, Any]]:
        """Process all due recurring expenses. Returns list of logged transactions."""
        due = self.get_due_today()
        logged = []
        
        for item in due:
            tx = self.log_recurring(item["id"])
            if tx:
                logged.append(tx)
        
        return logged
    
    def get_monthly_total(self) -> int:
        """Calculate total monthly cost of all recurring expenses."""
        total = 0
        
        for item in self.get_recurring():
            freq = item["frequency"]
            amount = item["amount"]
            
            if freq == "daily":
                total += amount * 30
            elif freq == "weekly":
                total += amount * 4
            elif freq == "biweekly":
                total += amount * 2
            elif freq == "monthly":
                total += amount
            elif freq == "quarterly":
                total += amount // 3
            elif freq == "yearly":
                total += amount // 12
        
        return total
    
    def get_report(self) -> str:
        """Generate a report of recurring expenses."""
        items = self.get_recurring()
        
        if not items:
            return "🔄 Recurring Expenses\n━━━━━━━━━━━━━━━━━━━━━\n\n📭 No recurring expenses set up.\n\nAdd one: finance recurring add 110k \"mobile\" monthly"
        
        monthly_total = self.get_monthly_total()
        
        lines = [
            "🔄 Recurring Expenses",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"💰 Monthly Total: {monthly_total:,} UZS",
            ""
        ]
        
        # Group by frequency
        by_freq = {}
        for item in items:
            freq = item["frequency"]
            if freq not in by_freq:
                by_freq[freq] = []
            by_freq[freq].append(item)
        
        freq_order = ["daily", "weekly", "biweekly", "monthly", "quarterly", "yearly"]
        
        for freq in freq_order:
            if freq not in by_freq:
                continue
            
            lines.append(f"📅 {freq.capitalize()}:")
            for item in by_freq[freq]:
                emoji = get_emoji(item["category"])
                next_due = datetime.fromisoformat(item["next_due"]).strftime("%m/%d")
                lines.append(f"   {emoji} {item['amount']:,} — {item['description']} (next: {next_due})")
            lines.append("")
        
        # Due today
        due = self.get_due_today()
        if due:
            lines.append("⚠️ DUE TODAY:")
            for item in due:
                emoji = get_emoji(item["category"])
                lines.append(f"   {emoji} {item['amount']:,} — {item['description']}")
        
        return "\n".join(lines)


# Global instance
_recurring: Optional[RecurringManager] = None

def get_recurring_manager(data_dir: Optional[Path] = None) -> RecurringManager:
    global _recurring
    if _recurring is None:
        if data_dir is None:
            env_dir = os.environ.get("FINANCE_DATA_DIR")
            if env_dir:
                data_dir = Path(env_dir)
        _recurring = RecurringManager(data_dir)
    return _recurring
