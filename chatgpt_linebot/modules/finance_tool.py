"""
Finance Tool — Bridge between chatbot and finance-tracker skill.
Each user gets their own isolated data directory under ~/.finance-tracker/users/<user_id>/
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import json

# Add finance-tracker lib to path
_finance_lib = os.path.join(os.path.dirname(__file__), '..', '..', 'finance-tracker-2.0.0', 'lib')
sys.path.insert(0, os.path.abspath(_finance_lib))

from storage import FinanceStorage
from categories import detect_category, get_emoji, list_categories
from reports import generate_report, list_recent, search_transactions
from parser import parse_amount, format_confirmation, format_error
from portfolio import Portfolio
from trends import analyze_trends, compare_periods, get_budget_status
from recurring import RecurringManager
from goals import GoalsManager
from currency import CurrencyConverter
from insights import get_insights, get_daily_summary, get_weekly_digest


# Per-user instance cache
_user_instances: Dict[str, Dict[str, Any]] = {}


def _get_user_data_dir(user_id: str) -> Path:
    """Get per-user data directory."""
    base = Path.home() / ".finance-tracker" / "users" / str(user_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _get_user_instances(user_id: str) -> Dict[str, Any]:
    """Get or create per-user finance instances."""
    if user_id in _user_instances:
        return _user_instances[user_id]

    data_dir = _get_user_data_dir(user_id)
    instances = {
        "storage": FinanceStorage(data_dir),
        "recurring": RecurringManager(data_dir),
        "goals": GoalsManager(data_dir),
        "portfolio": Portfolio(data_dir),
        "converter": CurrencyConverter(data_dir),
    }
    _user_instances[user_id] = instances
    return instances


def _parse_amount_for_user(amount_str: str, user_id: str) -> int:
    """Parse amount with per-user currency settings."""
    inst = _get_user_instances(user_id)
    default_currency = inst["storage"].get_currency()
    amount, _ = inst["converter"].parse_amount(amount_str, default_currency)
    return amount


def execute_finance_command(user_id: str, command_text: str) -> str:
    """
    Execute a finance command for a specific user.
    
    Args:
        user_id: LINE user/group/room ID
        command_text: The command string, e.g. "add 50k lunch" or "report last-month"
    
    Returns:
        Response text to send back to user
    """
    parts = command_text.strip().split()
    if not parts:
        return "❌ 請輸入指令，例如：記帳 add 50k 午餐"

    command = parts[0].lower()
    args = parts[1:]
    inst = _get_user_instances(user_id)
    storage = inst["storage"]
    currency = storage.get_currency()

    try:
        # ===== ADD =====
        if command == "add":
            if len(args) < 2:
                return "❌ 格式：add <金額> <描述>\n例如：add 50k 午餐"
            amount = _parse_amount_for_user(args[0], user_id)
            if not amount or amount <= 0:
                return "❌ 無效的金額"
            description = " ".join(args[1:]).strip('"\'')
            if not description:
                return "❌ 請輸入描述"
            tx = storage.add_transaction(amount, description)
            emoji = get_emoji(tx["category"])
            return f"✅ 已記帳：{emoji} {tx['amount']:,} {currency} — {tx['description']}"

        # ===== UNDO =====
        elif command == "undo":
            removed = storage.undo_last()
            if removed:
                emoji = get_emoji(removed["category"])
                return f"↩️ 已撤銷：{emoji} {removed['amount']:,} — {removed['description']}"
            return "❌ 沒有可以撤銷的交易"

        # ===== DELETE =====
        elif command == "delete":
            if not args:
                return "❌ 格式：delete <id>"
            try:
                tx_id = int(args[0])
            except ValueError:
                return "❌ 無效的交易 ID"
            if storage.delete_transaction(tx_id):
                return f"🗑️ 已刪除交易 #{tx_id}"
            return f"❌ 找不到交易 #{tx_id}"

        # ===== EDIT =====
        elif command == "edit":
            if not args:
                return "❌ 格式：edit <id> [--amount=X] [--desc=Y]"
            try:
                tx_id = int(args[0])
            except ValueError:
                return "❌ 無效的交易 ID"
            amount = None
            description = None
            category = None
            for arg in args[1:]:
                if arg.startswith("--amount="):
                    amount = _parse_amount_for_user(arg.split("=", 1)[1], user_id)
                elif arg.startswith("--desc="):
                    description = arg.split("=", 1)[1].strip('"\'')
                elif arg.startswith("--category="):
                    category = arg.split("=", 1)[1].strip('"\'')
            tx = storage.edit_transaction(tx_id, amount, description, category)
            if tx:
                emoji = get_emoji(tx["category"])
                return f"✏️ 已更新：{emoji} {tx['amount']:,} — {tx['description']}"
            return f"❌ 找不到交易 #{tx_id}"

        # ===== REPORT =====
        elif command == "report":
            period = args[0] if args else "month"
            extra_args = args[1:] if len(args) > 1 else []
            return generate_report(period, storage=storage, extra_args=extra_args)

        # ===== RECENT =====
        elif command == "recent":
            n = int(args[0]) if args else 5
            return list_recent(n, storage=storage)

        # ===== SEARCH =====
        elif command == "search":
            if not args:
                return "❌ 格式：search <關鍵字>"
            query = " ".join(args).strip('"\'')
            return search_transactions(query, storage=storage)

        # ===== CATEGORIES =====
        elif command == "categories":
            return list_categories()

        # ===== EXPORT =====
        elif command == "export":
            fmt = args[0] if args else "csv"
            if fmt == "csv":
                return storage.export_csv()
            elif fmt == "json":
                txs = storage.get_transactions()
                return json.dumps(txs, indent=2, ensure_ascii=False)
            return f"❌ 未知格式：{fmt}，請用 csv 或 json"

        # ===== CURRENCY =====
        elif command == "currency":
            if args:
                storage.set_currency(args[0].upper())
                return f"✅ 幣別已設為 {args[0].upper()}"
            return f"💱 目前幣別：{currency}"

        # ===== RATES =====
        elif command == "rates":
            converter = inst["converter"]
            if args:
                return converter.get_rate_info(args[0])
            return converter.get_rates_report()

        # ===== CONVERT =====
        elif command == "convert":
            if len(args) < 3:
                return "❌ 格式：convert 100 USD TWD"
            try:
                amount = float(args[0].replace('k', '000').replace('K', '000'))
            except ValueError:
                return "❌ 無效金額"
            from_curr = args[1].upper()
            to_curr = args[2].upper()
            converter = inst["converter"]
            converted, rate = converter.convert(amount, from_curr, to_curr)
            from_fmt = converter.format_amount(amount, from_curr)
            to_fmt = converter.format_amount(converted, to_curr)
            return f"💱 {from_fmt} = {to_fmt}\n   匯率：1 {from_curr} = {rate:.4f} {to_curr}"

        # ===== INCOME =====
        elif command == "income":
            if len(args) < 2:
                return "❌ 格式：income <金額> <描述>"
            amount = _parse_amount_for_user(args[0], user_id)
            if amount <= 0:
                return "❌ 無效金額"
            description = " ".join(args[1:]).strip('"\'')
            income_type = "other"
            desc_lower = description.lower()
            for keywords, itype in [
                (["salary", "wage", "paycheck", "薪水", "薪資"], "salary"),
                (["freelance", "gig", "contract", "接案"], "freelance"),
                (["business", "sales", "revenue", "營收"], "business"),
                (["dividend", "interest", "investment", "股息", "利息"], "investment"),
                (["gift", "bonus", "獎金", "禮物"], "gift"),
            ]:
                if any(w in desc_lower for w in keywords):
                    income_type = itype
                    break
            portfolio = inst["portfolio"]
            portfolio.add_income(amount, description, income_type)
            emoji = Portfolio.INCOME_TYPES.get(income_type, {}).get("emoji", "💰")
            return f"✅ 收入已記錄：{emoji} {amount:,} {currency} — {description}"

        # ===== ASSET =====
        elif command in ("asset", "portfolio", "networth"):
            portfolio = inst["portfolio"]
            if command in ("portfolio", "networth") or not args:
                return portfolio.get_portfolio_report()
            action = args[0].lower()
            if action == "add" and len(args) >= 3:
                name = args[1].strip('"\'')
                value = _parse_amount_for_user(args[2], user_id)
                asset_type = args[3] if len(args) > 3 else "other"
                portfolio.add_asset(name, value, asset_type)
                return f"✅ 資產已新增：{name} = {value:,} {currency}"
            elif action == "remove" and len(args) >= 2:
                name = args[1].strip('"\'')
                if portfolio.remove_asset(name):
                    return f"✅ 已移除：{name}"
                return f"❌ 找不到資產：{name}"
            elif action == "list":
                return portfolio.get_portfolio_report()
            return "❌ 格式：asset [add|remove|list]"

        # ===== RECURRING =====
        elif command in ("recurring", "recur", "sub"):
            recurring = inst["recurring"]
            if not args:
                return recurring.get_report()
            action = args[0].lower()
            if action == "add" and len(args) >= 4:
                amount = _parse_amount_for_user(args[1], user_id)
                description = args[2].strip('"\'')
                frequency = args[3].lower()
                day = None
                for arg in args[4:]:
                    if arg.startswith("--day="):
                        day = int(arg.split("=")[1])
                if frequency not in recurring.FREQUENCIES:
                    return f"❌ 無效頻率，可用：{', '.join(recurring.FREQUENCIES.keys())}"
                item = recurring.add_recurring(amount, description, frequency, day)
                emoji = get_emoji(item["category"])
                return f"✅ 定期支出已新增：{emoji} {amount:,} — {description} ({frequency})"
            elif action == "remove" and len(args) >= 2:
                if recurring.remove_recurring(args[1].strip('"\'')):
                    return "✅ 已移除定期支出"
                return "❌ 找不到該項目"
            elif action == "list":
                return recurring.get_report()
            elif action == "process":
                def _do_process():
                    return recurring.process_due()
                logged = _run_with_user_storage(user_id, _do_process)
                if logged:
                    lines = [f"✅ 已處理 {len(logged)} 筆定期支出"]
                    for tx in logged:
                        emoji = get_emoji(tx["category"])
                        lines.append(f"   {emoji} {tx['amount']:,} — {tx['description']}")
                    return "\n".join(lines)
                return "✅ 今天沒有到期的定期支出"
            elif action == "due":
                due = recurring.get_due_today()
                if due:
                    lines = ["🔄 今日到期："]
                    for item in due:
                        emoji = get_emoji(item["category"])
                        lines.append(f"   {emoji} {item['amount']:,} — {item['description']}")
                    return "\n".join(lines)
                return "✅ 今天沒有到期項目"
            return "❌ 格式：recurring [add|remove|list|process|due]"

        # ===== GOALS =====
        elif command in ("goal", "goals", "save", "saving"):
            goals = inst["goals"]
            if not args:
                return goals.get_report()
            action = args[0].lower()
            if action == "add" and len(args) >= 3:
                name = args[1].strip('"\'')
                target = _parse_amount_for_user(args[2], user_id)
                deadline = None
                current = 0
                for arg in args[3:]:
                    if arg.startswith("--by="):
                        deadline = arg.split("=")[1]
                    elif arg.startswith("--current="):
                        current = _parse_amount_for_user(arg.split("=")[1], user_id)
                goals.add_goal(name, target, deadline, current)
                result = f"🎯 目標已新增：{name}\n   目標金額：{target:,} {currency}"
                if deadline:
                    result += f"\n   截止日期：{deadline}"
                return result
            elif action == "update" and len(args) >= 3:
                name = args[1].strip('"\'')
                amount = _parse_amount_for_user(args[2], user_id)
                goal = goals.update_goal(name, amount)
                if goal:
                    progress = goals.get_goal_progress(goal)
                    result = f"✅ 已為 {goal['name']} 增加 {amount:,}"
                    result += f"\n   進度：{progress['current']:,} / {progress['target']:,} ({progress['percentage']:.0f}%)"
                    if goal.get("completed"):
                        result += "\n   🎉 目標達成！"
                    return result
                return f"❌ 找不到目標：{name}"
            elif action == "set" and len(args) >= 3:
                name = args[1].strip('"\'')
                amount = _parse_amount_for_user(args[2], user_id)
                goal = goals.set_goal_amount(name, amount)
                if goal:
                    progress = goals.get_goal_progress(goal)
                    return f"✅ {goal['name']} 已設為 {amount:,}\n   進度：{progress['percentage']:.0f}%"
                return f"❌ 找不到目標：{name}"
            elif action == "remove" and len(args) >= 2:
                name = args[1].strip('"\'')
                if goals.remove_goal(name):
                    return f"✅ 已移除目標：{name}"
                return f"❌ 找不到目標：{name}"
            elif action == "list":
                return goals.get_report()
            return "❌ 格式：goal [add|update|set|remove|list]"

        # ===== INSIGHTS / SUMMARY / DIGEST =====
        elif command == "insights":
            # insights uses global get_storage, need to monkey-patch temporarily
            return _run_with_user_storage(user_id, get_insights)
        elif command == "summary":
            return _run_with_user_storage(user_id, get_daily_summary)
        elif command == "digest":
            return _run_with_user_storage(user_id, get_weekly_digest)

        # ===== TRENDS / COMPARE / BUDGET =====
        elif command in ("trends", "analyze"):
            days = int(args[0]) if args else 90
            return _run_with_user_storage(user_id, lambda: analyze_trends(days))
        elif command == "compare":
            days = int(args[0]) if args else 30
            return _run_with_user_storage(user_id, lambda: compare_periods(days, days))
        elif command == "budget":
            if not args:
                return "❌ 格式：budget <每日金額>\n例如：budget 100k"
            daily = _parse_amount_for_user(args[0], user_id)
            if daily <= 0:
                return "❌ 無效金額"
            return _run_with_user_storage(user_id, lambda: get_budget_status(daily))

        # ===== HELP =====
        elif command == "help":
            return get_finance_help()

        else:
            return f"❌ 未知的記帳指令：{command}\n輸入 help 查看所有指令"

    except Exception as e:
        return f"❌ 執行錯誤：{e}"


def _run_with_user_storage(user_id: str, func):
    """
    Temporarily replace global storage/recurring/goals/portfolio/converter
    with per-user instances, then run func().
    This is needed for modules that use get_storage() internally.
    """
    import storage as storage_mod
    import recurring as recurring_mod
    import goals as goals_mod
    import portfolio as portfolio_mod
    import currency as currency_mod

    inst = _get_user_instances(user_id)

    # Save originals
    orig = {
        "storage": getattr(storage_mod, '_storage', None),
        "recurring": getattr(recurring_mod, '_recurring', None),
        "goals": getattr(goals_mod, '_goals', None),
        "portfolio": getattr(portfolio_mod, '_portfolio', None),
        "converter": getattr(currency_mod, '_converter', None),
    }

    try:
        storage_mod._storage = inst["storage"]
        recurring_mod._recurring = inst["recurring"]
        goals_mod._goals = inst["goals"]
        portfolio_mod._portfolio = inst["portfolio"]
        currency_mod._converter = inst["converter"]
        return func()
    finally:
        # Restore
        storage_mod._storage = orig["storage"]
        recurring_mod._recurring = orig["recurring"]
        goals_mod._goals = orig["goals"]
        portfolio_mod._portfolio = orig["portfolio"]
        currency_mod._converter = orig["converter"]


def get_finance_help() -> str:
    return """💰 記帳功能說明

📝 記帳：
  add <金額> <描述>        記一筆支出
  undo                    撤銷最後一筆
  edit <id> [--amount=X]  編輯交易
  delete <id>             刪除交易

📊 查詢：
  report [時段]           支出報告
  recent [n]              最近 n 筆交易
  search <關鍵字>         搜尋交易

⏰ 時段選項：
  today, yesterday, week, last-week
  month, last-month, last-2-months, last-3-months
  year, all, YYYY-MM, YYYY-MM-DD
  YYYY-MM-DD YYYY-MM-DD (日期範圍)

🔄 定期支出：
  recurring               查看定期支出
  recurring add <金額> <描述> <頻率>
  recurring process       處理今日到期

🎯 儲蓄目標：
  goal                    查看目標
  goal add <名稱> <目標金額> [--by=日期]
  goal update <名稱> <金額>

💱 幣別：
  currency [代碼]         查看/設定幣別
  rates [幣別]            匯率
  convert <金額> <從> <到>

💵 收入 & 資產：
  income <金額> <描述>
  asset add/remove/list
  portfolio

📈 分析：
  insights / summary / digest
  trends [天數] / compare [天數]
  budget <每日金額>

💡 金額格式：50000, 50k, $50, €100"""
