"""
Finance Tracker — Report Generation
Creates beautiful, readable spending reports
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple, List
import calendar

try:
    from .storage import FinanceStorage, get_storage
    from .categories import get_emoji, get_name
except ImportError:
    from storage import FinanceStorage, get_storage
    from categories import get_emoji, get_name


def _parse_period(period: str, extra_args: list = None) -> Tuple[Optional[datetime], Optional[datetime], str]:
    """
    Parse period string into (start_date, end_date, title).
    
    Supports:
      - today, yesterday, week, month, year, all
      - last-N-days, last-N-weeks, last-N-months (e.g. last-3-months, last-2-weeks)
      - last-week, last-month (shortcuts for last-1-week, last-1-month)
      - YYYY-MM (specific month)
      - YYYY-MM-DD (specific day)
      - YYYY-MM-DD YYYY-MM-DD (date range, end date via extra_args)
    """
    import re

    now = datetime.now()
    period_lower = period.lower()

    # Date range: period=YYYY-MM-DD, extra_args[0]=YYYY-MM-DD
    if extra_args and len(extra_args) >= 1:
        try:
            start = datetime.strptime(period, "%Y-%m-%d")
            end = datetime.strptime(extra_args[0], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            title = f"📊 Spending: {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"
            return start, end, title
        except ValueError:
            pass

    # Specific month: YYYY-MM
    if len(period) == 7 and period[4:5] == '-':
        try:
            year, month = int(period[:4]), int(period[5:7])
            start = datetime(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end = datetime(year, month, last_day, 23, 59, 59)
            title = f"📊 Spending for {start.strftime('%B %Y')}"
            return start, end, title
        except ValueError:
            pass

    # Specific day: YYYY-MM-DD
    if len(period) == 10 and period[4:5] == '-' and period[7:8] == '-':
        try:
            start = datetime.strptime(period, "%Y-%m-%d")
            end = start.replace(hour=23, minute=59, second=59)
            title = f"📊 Spending on {start.strftime('%b %d, %Y')}"
            return start, end, title
        except ValueError:
            pass

    # Named: today / yesterday
    if period_lower in ("today", "day"):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, f"📊 Today's Spending ({now.strftime('%b %d, %Y')})"

    if period_lower == "yesterday":
        yday = now - timedelta(days=1)
        start = yday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yday.replace(hour=23, minute=59, second=59)
        return start, end, f"📊 Yesterday's Spending ({yday.strftime('%b %d, %Y')})"

    # Current period: week / month / year
    if period_lower == "week":
        start = now - timedelta(days=7)
        return start, now, "📊 This Week's Spending"

    if period_lower == "month":
        start = now - timedelta(days=30)
        return start, now, "📊 This Month's Spending"

    if period_lower == "year":
        start = now - timedelta(days=365)
        return start, now, "📊 This Year's Spending"

    if period_lower == "all":
        return None, None, "📊 All-Time Spending"

    # Generic: last-N-days, last-N-weeks, last-N-months
    # Also supports shortcuts: last-week → last-1-week, last-month → last-1-month, last-day → last-1-day
    m = re.match(r'^last-(\d+-)?(day|week|month|year)s?$', period_lower)
    if m:
        n = int(m.group(1)[:-1]) if m.group(1) else 1
        unit = m.group(2)

        if unit == "day":
            # last-N-days: the N days before today
            end_date = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
            start_date = end_date - timedelta(days=n - 1)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            title = f"📊 Last {n} Day{'s' if n > 1 else ''} ({start_date.strftime('%m/%d')} ~ {end_date.strftime('%m/%d')})"
            return start_date, end_date, title

        elif unit == "week":
            # last-N-weeks: go back N complete calendar weeks (Mon-Sun)
            today_weekday = now.weekday()
            last_sun = now - timedelta(days=today_weekday + 1)
            target_mon = last_sun - timedelta(days=7 * n - 1)
            target_sun = target_mon + timedelta(days=6)
            start_date = target_mon.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = target_sun.replace(hour=23, minute=59, second=59)
            if n == 1:
                title = f"📊 Last Week ({start_date.strftime('%m/%d')} ~ {end_date.strftime('%m/%d')})"
            else:
                title = f"📊 {n} Weeks Ago ({start_date.strftime('%m/%d')} ~ {end_date.strftime('%m/%d')})"
            return start_date, end_date, title

        elif unit == "month":
            # last-N-months: the Nth month before current month
            # Walk back N months from the 1st of this month
            first_of_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            target_end = first_of_current
            for _ in range(n - 1):
                target_end = target_end.replace(day=1) - timedelta(days=1)
            # target_end is now in the (n-1)th previous month, we need nth
            target_end = target_end.replace(day=1) - timedelta(seconds=1)  # end of Nth month ago
            target_start = target_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if n == 1:
                title = f"📊 Last Month ({target_start.strftime('%B %Y')})"
            else:
                title = f"📊 {n} Months Ago ({target_start.strftime('%B %Y')})"
            return target_start, target_end, title

        elif unit == "year":
            first_of_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            target_end = first_of_year - timedelta(seconds=1)
            for _ in range(n - 1):
                target_end = target_end.replace(month=1, day=1, hour=0, minute=0, second=0) - timedelta(seconds=1)
            target_start = target_end.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            if n == 1:
                title = f"📊 Last Year ({target_start.year})"
            else:
                title = f"📊 {n} Years Ago ({target_start.year})"
            return target_start, target_end, title

    # Default: month
    start = now - timedelta(days=30)
    return start, now, "📊 This Month's Spending"


def _build_stats(transactions: list) -> dict:
    """Build stats dict from a filtered list of transactions."""
    if not transactions:
        return {"count": 0, "total": 0, "average": 0, "by_category": {}}

    total = sum(tx["amount"] for tx in transactions)
    by_category = {}
    for tx in transactions:
        cat = tx.get("category", "other")
        if cat not in by_category:
            by_category[cat] = {"amount": 0, "count": 0}
        by_category[cat]["amount"] += tx["amount"]
        by_category[cat]["count"] += 1

    by_category = dict(sorted(by_category.items(), key=lambda x: x[1]["amount"], reverse=True))

    return {
        "count": len(transactions),
        "total": total,
        "average": total // len(transactions),
        "by_category": by_category
    }


def generate_report(
    period: str = "month",
    storage: Optional[FinanceStorage] = None,
    extra_args: list = None
) -> str:
    """
    Generate a spending report for the given period.

    Supports: today, yesterday, week, last-week, month, last-month,
              last-2-months, last-3-months, year, all,
              YYYY-MM, YYYY-MM-DD, YYYY-MM-DD YYYY-MM-DD (range)
    """
    if storage is None:
        storage = get_storage()

    start_date, end_date, title = _parse_period(period, extra_args or [])
    currency = storage.get_currency()

    if start_date is None and end_date is None:
        # All-time: use existing get_stats
        stats = storage.get_stats(days=None)
    else:
        # Filter transactions by date range
        all_tx = storage.get_transactions()
        filtered = []
        for tx in all_tx:
            tx_date = datetime.fromisoformat(tx["date"])
            if start_date and tx_date < start_date:
                continue
            if end_date and tx_date > end_date:
                continue
            filtered.append(tx)
        stats = _build_stats(filtered)

    if stats["count"] == 0:
        return f"{title}\n━━━━━━━━━━━━━━━━━━━━━\n\n📭 No transactions found for this period."

    # Build report
    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━━",
        f"💵 Total: {stats['total']:,} {currency}",
        ""
    ]

    # Category breakdown
    for category, data in stats["by_category"].items():
        emoji = get_emoji(category)
        amount = data["amount"]
        count = data["count"]
        pct = (amount / stats["total"]) * 100

        lines.append(f"{emoji} {category.capitalize()}: {amount:,} {currency} ({pct:.1f}%) [{count} 筆]")

    lines.append("")
    lines.append(f"📝 {stats['count']} transactions")
    lines.append(f"📈 Average: {stats['average']:,} {currency}")

    # Transaction detail list
    # For date-range filtered reports, use the filtered list;
    # for all-time / named periods, re-fetch filtered transactions
    detail_txs = None
    if start_date is not None or end_date is not None:
        all_tx = storage.get_transactions()
        detail_txs = []
        for tx in all_tx:
            tx_date = datetime.fromisoformat(tx["date"])
            if start_date and tx_date < start_date:
                continue
            if end_date and tx_date > end_date:
                continue
            detail_txs.append(tx)
    else:
        detail_txs = storage.get_transactions()

    if detail_txs:
        lines.append("")
        lines.append("── 明細 ──")
        for tx in detail_txs[:30]:  # Limit to 30 items
            emoji = get_emoji(tx.get("category", "other"))
            date_str = datetime.fromisoformat(tx["date"]).strftime("%m/%d %H:%M")
            lines.append(f"  {date_str}  {emoji} {tx['amount']:,} {currency} — {tx['description']}")
        if len(detail_txs) > 30:
            lines.append(f"  ... 還有 {len(detail_txs) - 30} 筆")

    return "\n".join(lines)


def list_recent(
    n: int = 5,
    storage: Optional[FinanceStorage] = None
) -> str:
    """
    List recent transactions.
    
    Args:
        n: Number of transactions to show
        storage: Optional storage instance
    
    Returns:
        Formatted list string
    """
    if storage is None:
        storage = get_storage()
    
    transactions = storage.get_transactions(limit=n)
    currency = storage.get_currency()
    
    if not transactions:
        return "📭 No transactions yet.\n\nAdd one: finance add 50000 \"coffee\""
    
    lines = [f"📝 Recent Transactions (last {len(transactions)}):", ""]
    
    for tx in transactions:
        emoji = get_emoji(tx["category"])
        date = datetime.fromisoformat(tx["date"]).strftime("%m/%d %H:%M")
        amount = f"{tx['amount']:,}"
        
        lines.append(f"  {date}  {emoji} {amount} {currency} — {tx['description']}")
    
    return "\n".join(lines)


def search_transactions(
    query: str,
    storage: Optional[FinanceStorage] = None
) -> str:
    """
    Search transactions and return formatted results.
    
    Args:
        query: Search query
        storage: Optional storage instance
    
    Returns:
        Formatted search results
    """
    if storage is None:
        storage = get_storage()
    
    results = storage.search(query)
    currency = storage.get_currency()
    
    if not results:
        return f"🔍 No transactions found matching '{query}'"
    
    total = sum(tx["amount"] for tx in results)
    
    lines = [
        f"🔍 Search: '{query}'",
        f"Found {len(results)} transactions (total: {total:,} {currency})",
        ""
    ]
    
    for tx in results[:10]:  # Limit to 10 results
        emoji = get_emoji(tx["category"])
        date = datetime.fromisoformat(tx["date"]).strftime("%m/%d")
        
        lines.append(f"  {date}  {emoji} {tx['amount']:,} — {tx['description']}")
    
    if len(results) > 10:
        lines.append(f"\n  ... and {len(results) - 10} more")
    
    return "\n".join(lines)
