from collections import defaultdict
from decimal import Decimal

from shop.models import (
    BatchProfitSummary,
    InvestmentRound,
    Investor,
    InvestorWithdrawal,
    RoundBatch,
    RoundInvestment,
)


ZERO = Decimal("0")


def _share(value, percent):
    return (value or ZERO) * percent / Decimal("100") if percent else ZERO


def build_round_report(round_obj):
    investments = RoundInvestment.objects.filter(round=round_obj).select_related("investor")
    withdrawals = InvestorWithdrawal.objects.filter(round=round_obj).select_related("investor")
    batch_links = list(RoundBatch.objects.filter(round=round_obj))
    batch_numbers = [link.batch_number for link in batch_links]
    summaries = list(BatchProfitSummary.objects.filter(batch_number__in=batch_numbers))

    investment_by_investor = defaultdict(lambda: ZERO)
    withdrawal_by_investor = defaultdict(lambda: ZERO)
    investors = {}

    for investment in investments:
        investment_by_investor[investment.investor_id] += investment.amount
        investors[investment.investor_id] = investment.investor

    for withdrawal in withdrawals:
        withdrawal_by_investor[withdrawal.investor_id] += withdrawal.amount
        investors[withdrawal.investor_id] = withdrawal.investor

    total_investment = sum(investment_by_investor.values(), ZERO)
    totals = {
        "investment": total_investment,
        "withdrawals": sum(withdrawal_by_investor.values(), ZERO),
        "revenue": sum((summary.revenue for summary in summaries), ZERO),
        "amount_due": sum((summary.amount_due for summary in summaries), ZERO),
        "cost": sum((summary.cost for summary in summaries), ZERO),
        "gross_profit": sum((summary.gross_profit for summary in summaries), ZERO),
        "batch_expenses": sum((summary.batch_expenses for summary in summaries), ZERO),
        "net_profit": sum((summary.net_profit for summary in summaries), ZERO),
        "stock_value": sum((summary.stock_value for summary in summaries), ZERO),
        "purchased_qty": sum((summary.purchased_qty for summary in summaries), 0),
        "sold_qty": sum((summary.sold_qty for summary in summaries), 0),
        "remaining_qty": sum((summary.remaining_qty for summary in summaries), 0),
    }
    totals["purchase_cost"] = totals["cost"] + totals["stock_value"]
    totals["cash_balance"] = (
        totals["investment"] -
        totals["purchase_cost"] -
        totals["batch_expenses"] +
        totals["revenue"]
    )
    totals["business_value"] = (
        totals["cash_balance"] +
        totals["amount_due"] +
        totals["stock_value"]
    )
    totals["current_equity"] = totals["business_value"] - totals["withdrawals"]

    investor_rows = []
    for investor_id, investor in sorted(investors.items(), key=lambda item: item[1].name.lower()):
        investment = investment_by_investor[investor_id]
        ownership_percent = (
            investment * Decimal("100") / total_investment
            if total_investment > 0 else ZERO
        )
        withdrawn = withdrawal_by_investor[investor_id]
        equity_before_withdrawal = _share(totals["business_value"], ownership_percent)
        investor_rows.append({
            "investor": investor,
            "investment": investment,
            "withdrawn": withdrawn,
            "ownership_percent": ownership_percent,
            "revenue_share": _share(totals["revenue"], ownership_percent),
            "due_share": _share(totals["amount_due"], ownership_percent),
            "cost_share": _share(totals["cost"], ownership_percent),
            "cash_share": _share(totals["cash_balance"], ownership_percent),
            "profit_share": _share(totals["net_profit"], ownership_percent),
            "stock_share": _share(totals["stock_value"], ownership_percent),
            "equity_before_withdrawal": equity_before_withdrawal,
            "current_equity": equity_before_withdrawal - withdrawn,
        })

    batch_rows = []
    for summary in sorted(summaries, key=lambda item: item.batch_number):
        batch_rows.append({
            "summary": summary,
            "business_value": summary.revenue + summary.amount_due + summary.stock_value - summary.batch_expenses,
            "investors": [
                {
                    "investor": row["investor"],
                    "ownership_percent": row["ownership_percent"],
                    "profit_share": _share(summary.net_profit, row["ownership_percent"]),
                    "stock_share": _share(summary.stock_value, row["ownership_percent"]),
                    "due_share": _share(summary.amount_due, row["ownership_percent"]),
                }
                for row in investor_rows
            ],
        })

    return {
        "round": round_obj,
        "totals": totals,
        "investor_rows": investor_rows,
        "batch_rows": batch_rows,
        "batch_links": batch_links,
    }


def build_investor_equity_report():
    rounds = []
    totals = {
        "investment": ZERO,
        "withdrawals": ZERO,
        "profit_share": ZERO,
        "stock_share": ZERO,
        "cash_share": ZERO,
        "due_share": ZERO,
        "current_equity": ZERO,
    }
    investor_totals = {}

    for investor in Investor.objects.all():
        investor_totals[investor.id] = {
            "investor": investor,
            "investment": ZERO,
            "withdrawn": ZERO,
            "profit_share": ZERO,
            "stock_share": ZERO,
            "cash_share": ZERO,
            "due_share": ZERO,
            "current_equity": ZERO,
        }

    for round_obj in InvestmentRound.objects.all():
        report = build_round_report(round_obj)
        rounds.append(report)
        for row in report["investor_rows"]:
            investor_total = investor_totals.setdefault(row["investor"].id, {
                "investor": row["investor"],
                "investment": ZERO,
                "withdrawn": ZERO,
                "profit_share": ZERO,
                "stock_share": ZERO,
                "cash_share": ZERO,
                "due_share": ZERO,
                "current_equity": ZERO,
            })
            investor_total["investment"] += row["investment"]
            investor_total["withdrawn"] += row["withdrawn"]
            investor_total["profit_share"] += row["profit_share"]
            investor_total["stock_share"] += row["stock_share"]
            investor_total["cash_share"] += row["cash_share"]
            investor_total["due_share"] += row["due_share"]
            investor_total["current_equity"] += row["current_equity"]

    for row in investor_totals.values():
        totals["investment"] += row["investment"]
        totals["withdrawals"] += row["withdrawn"]
        totals["profit_share"] += row["profit_share"]
        totals["stock_share"] += row["stock_share"]
        totals["cash_share"] += row["cash_share"]
        totals["due_share"] += row["due_share"]
        totals["current_equity"] += row["current_equity"]

    return {
        "rounds": rounds,
        "investor_rows": sorted(
            investor_totals.values(),
            key=lambda item: item["investor"].name.lower(),
        ),
        "totals": totals,
    }


def available_batch_numbers():
    assigned = RoundBatch.objects.values_list("batch_number", flat=True)
    return (
        BatchProfitSummary.objects.exclude(batch_number__in=assigned)
        .order_by("batch_number")
        .values_list("batch_number", flat=True)
    )
