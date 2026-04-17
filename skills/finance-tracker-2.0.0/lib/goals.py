"""
Finance Tracker — Savings Goals
Track progress towards financial goals
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from .portfolio import get_portfolio
except ImportError:
    from portfolio import get_portfolio


class GoalsManager:
    """Manage savings goals and track progress."""
    
    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path.home() / ".finance-tracker"
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.goals_file = self.data_dir / "goals.json"
        self._init_file()
    
    def _init_file(self):
        if not self.goals_file.exists():
            self._save({
                "goals": [],
                "created_at": datetime.now().isoformat()
            })
    
    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.goals_file) as f:
                return json.load(f)
        except:
            return {"goals": []}
    
    def _save(self, data: Dict[str, Any]):
        with open(self.goals_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def add_goal(
        self,
        name: str,
        target_amount: int,
        deadline: Optional[str] = None,
        current_amount: int = 0,
        priority: str = "medium"
    ) -> Dict[str, Any]:
        """
        Add a new savings goal.
        
        Args:
            name: Name of the goal (e.g., "New Laptop")
            target_amount: Target amount to save
            deadline: Target date (ISO format or "YYYY-MM-DD")
            current_amount: Amount already saved towards this goal
            priority: low, medium, high
        """
        data = self._load()
        
        # Check for duplicate
        existing = next((g for g in data["goals"] if g["name"].lower() == name.lower()), None)
        if existing:
            # Update existing
            existing["target_amount"] = target_amount
            if deadline:
                existing["deadline"] = deadline
            existing["current_amount"] = current_amount
            existing["priority"] = priority
            existing["updated_at"] = datetime.now().isoformat()
            self._save(data)
            return existing
        
        goal = {
            "id": len(data["goals"]) + 1,
            "name": name,
            "target_amount": target_amount,
            "current_amount": current_amount,
            "deadline": deadline,
            "priority": priority,
            "completed": False,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        data["goals"].append(goal)
        self._save(data)
        return goal
    
    def update_goal(self, id_or_name: str, amount: int) -> Optional[Dict[str, Any]]:
        """Add to the current amount of a goal."""
        data = self._load()
        
        for goal in data["goals"]:
            if str(goal["id"]) == str(id_or_name) or goal["name"].lower() == id_or_name.lower():
                goal["current_amount"] += amount
                goal["updated_at"] = datetime.now().isoformat()
                
                # Check if completed
                if goal["current_amount"] >= goal["target_amount"]:
                    goal["completed"] = True
                    goal["completed_at"] = datetime.now().isoformat()
                
                self._save(data)
                return goal
        
        return None
    
    def set_goal_amount(self, id_or_name: str, amount: int) -> Optional[Dict[str, Any]]:
        """Set the current amount of a goal (not add)."""
        data = self._load()
        
        for goal in data["goals"]:
            if str(goal["id"]) == str(id_or_name) or goal["name"].lower() == id_or_name.lower():
                goal["current_amount"] = amount
                goal["updated_at"] = datetime.now().isoformat()
                
                if goal["current_amount"] >= goal["target_amount"]:
                    goal["completed"] = True
                    goal["completed_at"] = datetime.now().isoformat()
                
                self._save(data)
                return goal
        
        return None
    
    def remove_goal(self, id_or_name: str) -> bool:
        """Remove a goal."""
        data = self._load()
        original_len = len(data["goals"])
        
        data["goals"] = [
            g for g in data["goals"]
            if str(g["id"]) != str(id_or_name) and g["name"].lower() != id_or_name.lower()
        ]
        
        if len(data["goals"]) < original_len:
            self._save(data)
            return True
        return False
    
    def get_goals(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all goals."""
        data = self._load()
        goals = data["goals"]
        
        if active_only:
            goals = [g for g in goals if not g.get("completed", False)]
        
        # Sort by priority and deadline
        priority_order = {"high": 0, "medium": 1, "low": 2}
        return sorted(goals, key=lambda x: (
            priority_order.get(x.get("priority", "medium"), 1),
            x.get("deadline") or "9999-12-31"
        ))
    
    def get_goal_progress(self, goal: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate progress details for a goal."""
        current = goal["current_amount"]
        target = goal["target_amount"]
        remaining = max(0, target - current)
        percentage = min(100, (current / target * 100)) if target > 0 else 0
        
        result = {
            "current": current,
            "target": target,
            "remaining": remaining,
            "percentage": percentage,
            "completed": current >= target
        }
        
        # Calculate required savings rate if deadline exists
        if goal.get("deadline") and remaining > 0:
            try:
                deadline = datetime.fromisoformat(goal["deadline"].replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=None)
                    
                now = datetime.now()
                days_left = (deadline.date() - now.date()).days
                
                if days_left > 0:
                    result["days_left"] = days_left
                    result["daily_required"] = remaining // days_left
                    result["weekly_required"] = (remaining * 7) // days_left
                    result["monthly_required"] = (remaining * 30) // days_left
                elif days_left <= 0:
                    result["overdue"] = True
                    result["days_overdue"] = abs(days_left)
            except:
                pass
        
        return result
    
    def get_report(self) -> str:
        """Generate a goals report."""
        goals = self.get_goals(active_only=False)
        active = [g for g in goals if not g.get("completed", False)]
        completed = [g for g in goals if g.get("completed", False)]
        
        if not goals:
            return "🎯 Savings Goals\n━━━━━━━━━━━━━━━━━━━━━\n\n📭 No goals set.\n\nAdd one: finance goal add \"Laptop\" 5000000 --by=2026-06-01"
        
        lines = [
            "🎯 Savings Goals",
            "━━━━━━━━━━━━━━━━━━━━━"
        ]
        
        # Active goals
        if active:
            total_target = sum(g["target_amount"] for g in active)
            total_saved = sum(g["current_amount"] for g in active)
            
            lines.append(f"💰 Total Saved: {total_saved:,} / {total_target:,} UZS")
            lines.append("")
            
            for goal in active:
                progress = self.get_goal_progress(goal)
                pct = progress["percentage"]
                
                # Progress bar
                filled = int(pct / 10)
                bar = "█" * filled + "░" * (10 - filled)
                
                priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(goal.get("priority", "medium"), "🟡")
                
                lines.append(f"{priority_emoji} **{goal['name']}**")
                lines.append(f"   [{bar}] {pct:.0f}%")
                lines.append(f"   {progress['current']:,} / {progress['target']:,} UZS")
                
                if progress.get("overdue"):
                    lines.append(f"   ⚠️ Overdue by {progress['days_overdue']} days!")
                elif progress.get("days_left"):
                    lines.append(f"   📅 {progress['days_left']} days left — need {progress['daily_required']:,}/day")
                
                lines.append("")
        
        # Completed goals
        if completed:
            lines.append("✅ Completed:")
            for goal in completed[-3:]:  # Show last 3
                lines.append(f"   🏆 {goal['name']} ({goal['target_amount']:,} UZS)")
        
        return "\n".join(lines)
    
    def get_daily_target(self) -> int:
        """Calculate total daily savings needed across all goals."""
        total = 0
        
        for goal in self.get_goals():
            progress = self.get_goal_progress(goal)
            if progress.get("daily_required"):
                total += progress["daily_required"]
        
        return total


# Global instance
_goals: Optional[GoalsManager] = None

def get_goals_manager(data_dir: Optional[Path] = None) -> GoalsManager:
    global _goals
    if _goals is None:
        if data_dir is None:
            env_dir = os.environ.get("FINANCE_DATA_DIR")
            if env_dir:
                data_dir = Path(env_dir)
        _goals = GoalsManager(data_dir)
    return _goals
