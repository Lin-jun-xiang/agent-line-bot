"""
Finance Tracker — Smart Insights
Analyze spending patterns and provide actionable alerts
"""

from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Any, Optional

try:
    from .storage import get_storage
    from .categories import get_emoji
    from .recurring import get_recurring_manager
    from .goals import get_goals_manager
except ImportError:
    from storage import get_storage
    from categories import get_emoji
    from recurring import get_recurring_manager
    from goals import get_goals_manager


def get_spending_velocity(days: int = 30) -> Dict[str, Any]:
    """Calculate spending velocity and project end-of-period totals."""
    storage = get_storage()
    
    transactions = storage.get_transactions(days=days)
    if not transactions:
        return {"has_data": False}
    
    total = sum(tx["amount"] for tx in transactions)
    
    # Get actual days with transactions
    dates = set()
    for tx in transactions:
        dt = datetime.fromisoformat(tx["date"])
        dates.add(dt.date())
    
    if len(dates) == 0:
        return {"has_data": False}
    
    # Calculate daily average based on actual spending days
    daily_avg = total / len(dates)
    
    # Project to end of month
    now = datetime.now()
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    days_left_in_month = (next_month - now).days
    
    # Current month spending
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_tx = [tx for tx in transactions if datetime.fromisoformat(tx["date"]) >= month_start]
    month_total = sum(tx["amount"] for tx in month_tx)
    days_elapsed = now.day
    
    projected_month_total = month_total + (daily_avg * days_left_in_month)
    
    return {
        "has_data": True,
        "daily_avg": int(daily_avg),
        "weekly_avg": int(daily_avg * 7),
        "month_so_far": int(month_total),
        "days_elapsed": days_elapsed,
        "days_left": days_left_in_month,
        "projected_month": int(projected_month_total)
    }


def compare_to_last_period(days: int = 7) -> Dict[str, Any]:
    """Compare current period to previous period."""
    storage = get_storage()
    
    now = datetime.now()
    current_cutoff = now.timestamp() - (days * 86400)
    previous_cutoff = current_cutoff - (days * 86400)
    
    all_tx = storage.get_transactions()
    
    current = [tx for tx in all_tx if tx["timestamp"] >= current_cutoff]
    previous = [tx for tx in all_tx if previous_cutoff <= tx["timestamp"] < current_cutoff]
    
    current_total = sum(tx["amount"] for tx in current)
    previous_total = sum(tx["amount"] for tx in previous)
    
    if previous_total == 0:
        change_pct = 100 if current_total > 0 else 0
    else:
        change_pct = ((current_total - previous_total) / previous_total) * 100
    
    # Compare by category
    current_by_cat = defaultdict(int)
    previous_by_cat = defaultdict(int)
    
    for tx in current:
        current_by_cat[tx["category"]] += tx["amount"]
    for tx in previous:
        previous_by_cat[tx["category"]] += tx["amount"]
    
    category_changes = {}
    all_cats = set(current_by_cat.keys()) | set(previous_by_cat.keys())
    
    for cat in all_cats:
        curr = current_by_cat.get(cat, 0)
        prev = previous_by_cat.get(cat, 0)
        
        if prev == 0:
            pct = 100 if curr > 0 else 0
        else:
            pct = ((curr - prev) / prev) * 100
        
        category_changes[cat] = {
            "current": curr,
            "previous": prev,
            "change_pct": pct,
            "increased": curr > prev
        }
    
    return {
        "period_days": days,
        "current_total": current_total,
        "previous_total": previous_total,
        "change_pct": change_pct,
        "increased": current_total > previous_total,
        "by_category": category_changes
    }


def detect_anomalies(days: int = 30) -> List[Dict[str, Any]]:
    """Detect unusual spending patterns."""
    storage = get_storage()
    transactions = storage.get_transactions(days=days)
    
    if len(transactions) < 5:
        return []
    
    anomalies = []
    
    # Calculate average and std dev
    amounts = [tx["amount"] for tx in transactions]
    avg = sum(amounts) / len(amounts)
    variance = sum((x - avg) ** 2 for x in amounts) / len(amounts)
    std_dev = variance ** 0.5
    
    # Find transactions > 2 std devs above average
    threshold = avg + (2 * std_dev)
    
    for tx in transactions:
        if tx["amount"] > threshold:
            anomalies.append({
                "type": "large_expense",
                "transaction": tx,
                "amount": tx["amount"],
                "threshold": threshold,
                "message": f"Unusually large expense: {tx['amount']:,} on {tx['description']}"
            })
    
    # Detect category spikes
    comparison = compare_to_last_period(days // 2)
    for cat, data in comparison["by_category"].items():
        if data["change_pct"] > 50 and data["current"] > avg * 3:
            anomalies.append({
                "type": "category_spike",
                "category": cat,
                "change_pct": data["change_pct"],
                "current": data["current"],
                "message": f"{cat.capitalize()} spending up {data['change_pct']:.0f}% vs last period"
            })
    
    return anomalies


def get_insights() -> str:
    """Generate comprehensive insights report."""
    storage = get_storage()
    currency = storage.get_currency()
    
    lines = [
        "💡 Smart Insights",
        "━━━━━━━━━━━━━━━━━━━━━"
    ]
    
    # Spending velocity
    velocity = get_spending_velocity()
    if velocity.get("has_data"):
        lines.append("")
        lines.append("📈 **Spending Velocity**")
        lines.append(f"   Daily avg: {velocity['daily_avg']:,} {currency}")
        lines.append(f"   This month so far: {velocity['month_so_far']:,} {currency}")
        lines.append(f"   Projected month total: {velocity['projected_month']:,} {currency}")
    
    # Period comparison
    comparison = compare_to_last_period(7)
    lines.append("")
    lines.append("📊 **This Week vs Last Week**")
    
    if comparison["increased"]:
        emoji = "📈"
        direction = "UP"
    else:
        emoji = "📉"
        direction = "DOWN"
    
    lines.append(f"   {emoji} Spending {direction} {abs(comparison['change_pct']):.0f}%")
    lines.append(f"   This week: {comparison['current_total']:,} {currency}")
    lines.append(f"   Last week: {comparison['previous_total']:,} {currency}")
    
    # Category changes
    significant_changes = [
        (cat, data) for cat, data in comparison["by_category"].items()
        if abs(data["change_pct"]) > 30 and data["current"] > 10000
    ]
    
    if significant_changes:
        lines.append("")
        lines.append("🏷️ **Notable Category Changes**")
        for cat, data in sorted(significant_changes, key=lambda x: abs(x[1]["change_pct"]), reverse=True)[:3]:
            emoji = get_emoji(cat)
            arrow = "↑" if data["increased"] else "↓"
            lines.append(f"   {emoji} {cat}: {arrow} {abs(data['change_pct']):.0f}%")
    
    # Anomalies
    anomalies = detect_anomalies()
    if anomalies:
        lines.append("")
        lines.append("⚠️ **Alerts**")
        for anomaly in anomalies[:3]:
            lines.append(f"   • {anomaly['message']}")
    
    # Recurring expenses due
    try:
        recurring = get_recurring_manager()
        due = recurring.get_due_today()
        if due:
            lines.append("")
            lines.append("🔄 **Due Today**")
            for item in due:
                emoji = get_emoji(item["category"])
                lines.append(f"   {emoji} {item['amount']:,} — {item['description']}")
    except:
        pass
    
    # Goals progress
    try:
        goals_mgr = get_goals_manager()
        goals = goals_mgr.get_goals()
        if goals:
            daily_target = goals_mgr.get_daily_target()
            if daily_target > 0:
                lines.append("")
                lines.append("🎯 **Savings Goals**")
                lines.append(f"   Need to save: {daily_target:,} {currency}/day")
                
                # Show most urgent goal
                for goal in goals[:1]:
                    progress = goals_mgr.get_goal_progress(goal)
                    if progress.get("days_left"):
                        lines.append(f"   Next deadline: {goal['name']} in {progress['days_left']} days")
    except:
        pass
    
    return "\n".join(lines)


def get_daily_summary() -> str:
    """Generate a quick daily summary for notifications."""
    storage = get_storage()
    currency = storage.get_currency()
    
    # Today's spending
    today_tx = storage.get_transactions(days=1)
    today_total = sum(tx["amount"] for tx in today_tx)
    
    # This week
    week_tx = storage.get_transactions(days=7)
    week_total = sum(tx["amount"] for tx in week_tx)
    
    # Velocity
    velocity = get_spending_velocity()
    
    lines = [
        f"💰 Today: {today_total:,} {currency}",
        f"📅 This week: {week_total:,} {currency}",
    ]
    
    if velocity.get("has_data"):
        lines.append(f"📊 Daily avg: {velocity['daily_avg']:,} {currency}")
    
    # Alerts
    anomalies = detect_anomalies(7)
    if anomalies:
        lines.append(f"⚠️ {len(anomalies)} alert(s)")
    
    return "\n".join(lines)


def get_weekly_digest() -> str:
    """Generate a weekly digest for scheduled reports."""
    storage = get_storage()
    currency = storage.get_currency()
    
    comparison = compare_to_last_period(7)
    velocity = get_spending_velocity()
    
    lines = [
        "📊 Weekly Finance Digest",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"💵 This week: {comparison['current_total']:,} {currency}",
        f"💵 Last week: {comparison['previous_total']:,} {currency}",
    ]
    
    if comparison["increased"]:
        lines.append(f"📈 Up {comparison['change_pct']:.0f}%")
    else:
        lines.append(f"📉 Down {abs(comparison['change_pct']):.0f}%")
    
    # Top categories
    lines.append("")
    lines.append("🏷️ Top Categories:")
    
    sorted_cats = sorted(
        comparison["by_category"].items(),
        key=lambda x: x[1]["current"],
        reverse=True
    )[:5]
    
    for cat, data in sorted_cats:
        emoji = get_emoji(cat)
        lines.append(f"   {emoji} {cat}: {data['current']:,}")
    
    # Projection
    if velocity.get("has_data"):
        lines.append("")
        lines.append(f"📅 Month projection: {velocity['projected_month']:,} {currency}")
    
    return "\n".join(lines)
