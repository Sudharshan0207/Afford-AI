"""Deterministic cash-flow forecaster for the Buy or Wait? challenge.

Run from the repository root with the bundled Python runtime (or Python 3.10+):
    python code/main.py
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
HORIZON_DAYS = 90
OUTPUT_COLUMNS = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan",
    "earliest_date_for_full_payment", "spending_changes_needed",
    "decision_explanation",
]


def parts(value: object) -> set[str]:
    return {x.strip() for x in str(value or "").split("|") if x.strip() and x != "nan"}


def money(value: float) -> str:
    """Stable amount format without insignificant trailing zeroes."""
    value = round(float(value), 2)
    return f"{value:.2f}".rstrip("0").rstrip(".")


class Forecaster:
    def __init__(self) -> None:
        self.profiles = pd.read_csv(DATA / "financial_profiles.csv").set_index("user_id")
        self.events = pd.read_csv(DATA / "financial_events.csv")
        self.requests = pd.read_csv(DATA / "requests.csv")
        self.options = pd.read_csv(DATA / "request_payment_options.csv")
        self.messages = pd.read_csv(DATA / "messages.csv")
        self.rates = pd.read_csv(DATA / "exchange_rates.csv")
        for column in ("event_date", "settlement_date"):
            self.events[column] = pd.to_datetime(self.events[column], errors="coerce")
        self.options["first_payment_date"] = pd.to_datetime(self.options["first_payment_date"], errors="coerce")
        self.rates["rate_date"] = pd.to_datetime(self.rates["rate_date"], errors="coerce")
        self.rate_map = {(r.rate_date.date(), r.from_currency, r.to_currency): float(r.rate)
                         for r in self.rates.itertuples()}

    def converted(self, row: pd.Series, home: str, day: pd.Timestamp | None = None) -> float | None:
        if pd.isna(row.amount):
            return None
        amount = float(row.amount)
        currency = row.currency
        if currency == home:
            return amount
        date = (day or row.settlement_date or row.event_date)
        if pd.isna(date):
            return None
        key = (pd.Timestamp(date).date(), currency, home)
        if key in self.rate_map:
            return amount * self.rate_map[key]
        # The provided rate dates are sparse. Use the latest dated supplied rate, never a live rate.
        candidates = [(d, rate) for (d, src, dst), rate in self.rate_map.items()
                      if src == currency and dst == home and d <= pd.Timestamp(date).date()]
        return amount * max(candidates, default=(None, None))[1] if candidates else None

    def user_events(self, user_id: str) -> pd.DataFrame:
        return self.events[self.events.user_id.eq(user_id)].copy()

    @staticmethod
    def is_cash(row: pd.Series) -> bool:
        return row.status not in {"cancelled", "failed"} and row.event_type != "investment_value"

    def recurring_streams(self, events: pd.DataFrame, request_day: pd.Timestamp, home: str) -> list[dict]:
        """Infer cadence from repeat cash spending/income, without turning isolated events into recurring ones."""
        past = events[(events.settlement_date <= request_day) & events.status.eq("settled")].copy()
        # A scheduled salary is a confirmed next occurrence and can establish the cadence when
        # the history is short (for example, a new job or a prorated first payroll).
        confirmed_salary = events[(events.category.eq("salary")) & (events.direction.eq("credit"))
                                  & (events.status.eq("scheduled"))].copy()
        past = pd.concat([past, confirmed_salary], ignore_index=True)
        past = past[past.apply(self.is_cash, axis=1)]
        streams: list[dict] = []
        # Stable categories may have varying merchant descriptions, so category+direction is the durable key.
        for (category, direction), group in past.groupby(["category", "direction"], dropna=False):
            if str(category) in {"windfall", "investment", "refund"}:
                continue
            group = group.sort_values("settlement_date").tail(12)
            if len(group) < 2:
                continue
            dates = group.settlement_date.dropna().sort_values().tolist()
            gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
            gaps = [g for g in gaps if 2 <= g <= 45]
            if not gaps:
                continue
            cadence = int(round(float(pd.Series(gaps).median())))
            # A single distant pair is weak evidence except for salary, rent, utilities and subscriptions.
            if len(gaps) < 2 and str(category) not in {"salary", "rent", "utilities", "insurance", "debt_repayment"}:
                continue
            values = [self.converted(row, home) for _, row in group.tail(3).iterrows()]
            values = [v for v in values if v is not None]
            if not values:
                continue
            streams.append({
                "key": (str(category), str(direction)), "date": dates[-1], "cadence": cadence,
                "amount": float(pd.Series(values).median()), "event_id": str(group.iloc[-1].event_id),
                "flexibility": str(group.iloc[-1].flexibility), "category": str(category),
                "direction": str(direction),
            })
        return streams

    def baseline(self, request: pd.Series, skip_streams: set[str] | None = None) -> tuple[dict[pd.Timestamp, float], list[dict]]:
        profile = self.profiles.loc[request.user_id]
        home = profile.home_currency
        start = pd.Timestamp(request.request_date).normalize()
        end = start + timedelta(days=HORIZON_DAYS)
        events = self.user_events(request.user_id)
        flows: dict[pd.Timestamp, float] = defaultdict(float)
        streams = self.recurring_streams(events, start, home)
        skip_streams = skip_streams or set()

        # Reserve future pending/scheduled debits. Scheduled credits count only when they are salary.
        scheduled_salary_days: set[pd.Timestamp] = set()
        for _, row in events.iterrows():
            if not self.is_cash(row):
                continue
            day = row.settlement_date
            if pd.isna(day) or day < start or day > end:
                continue
            amount = self.converted(row, home, day)
            if amount is None:
                continue
            if row.direction == "debit" and row.status in {"pending", "scheduled"}:
                flows[day.normalize()] -= amount
            elif row.direction == "credit" and row.status == "scheduled" and row.category == "salary":
                flows[day.normalize()] += amount
                scheduled_salary_days.add(day.normalize())

        # Project independently inferred streams, avoiding an extra projection on a supplied future event date.
        for stream in streams:
            if stream["event_id"] in skip_streams:
                continue
            day = stream["date"] + timedelta(days=stream["cadence"])
            while day <= end:
                if day >= start:
                    if stream["category"] == "salary" and day.normalize() in scheduled_salary_days:
                        day += timedelta(days=stream["cadence"])
                        continue
                    sign = 1 if stream["direction"] == "credit" else -1
                    flows[day.normalize()] += sign * stream["amount"]
                day += timedelta(days=stream["cadence"])
        return flows, streams

    @staticmethod
    def minimum_balance(balance: float, flows: dict[pd.Timestamp, float], start: pd.Timestamp,
                        payments: list[tuple[pd.Timestamp, float]] | None = None) -> float:
        deltas = defaultdict(float, flows)
        for day, amount in payments or []:
            deltas[pd.Timestamp(day).normalize()] -= amount
        low = balance
        current = balance
        for day in sorted(deltas):
            if day >= start:
                current += deltas[day]
                low = min(low, current)
        return low

    def earliest_full_date(self, request: pd.Series, flows: dict[pd.Timestamp, float]) -> pd.Timestamp | None:
        profile = self.profiles.loc[request.user_id]
        start = pd.Timestamp(request.request_date).normalize()
        amount = float(request.requested_amount)
        floor = float(profile.minimum_balance_to_keep)
        for offset in range(HORIZON_DAYS + 1):
            day = start + timedelta(days=offset)
            if self.minimum_balance(float(profile.current_available_balance), flows, start, [(day, amount)]) >= floor - 0.005:
                return day
        return None

    def flexible_changes(self, request: pd.Series, flows: dict[pd.Timestamp, float], streams: list[dict]) -> tuple[set[str], list[str]]:
        """Greedily stop permitted recurring costs only when it enables a full immediate payment."""
        profile = self.profiles.loc[request.user_id]
        allowed = parts(profile.expense_categories_user_is_willing_to_stop)
        candidates = [s for s in streams if s["direction"] == "debit" and s["category"] in allowed
                      and "stoppable" in s["flexibility"]]
        candidates.sort(key=lambda s: s["amount"] / max(s["cadence"], 1), reverse=True)
        selected: set[str] = set()
        actions: list[str] = []
        for stream in candidates[:3]:
            selected.add(stream["event_id"])
            actions.append(f"stop:{stream['event_id']}")
            changed, _ = self.baseline(request, selected)
            earliest = self.earliest_full_date(request, changed)
            if earliest is not None and earliest == pd.Timestamp(request.request_date).normalize():
                return selected, actions
        return set(), []

    def option_schedule(self, option: pd.Series) -> list[tuple[pd.Timestamp, float]]:
        first = option.first_payment_date
        count = int(option.number_of_payments)
        frequency = int(option.payment_frequency_days) if not pd.isna(option.payment_frequency_days) else 0
        amount = float(option.payment_amount)
        return [(first + timedelta(days=i * frequency), amount) for i in range(count)]

    def recommend(self, request: pd.Series) -> dict:
        profile = self.profiles.loc[request.user_id]
        start = pd.Timestamp(request.request_date).normalize()
        deadline = pd.Timestamp(request.desired_completion_date).normalize()
        amount = float(request.requested_amount)
        balance = float(profile.current_available_balance)
        floor = float(profile.minimum_balance_to_keep)
        flows, streams = self.baseline(request)
        baseline_low = self.minimum_balance(balance, flows, start)
        safe_now = min(amount, max(0.0, baseline_low - floor))
        earliest = self.earliest_full_date(request, flows)
        accepted = parts(profile.payment_methods_user_will_consider)
        currency = profile.home_currency

        def result(status: str, method: str, payments: list[tuple[pd.Timestamp, float]], changes: list[str] | None = None) -> dict:
            plan = "|".join(f"{pd.Timestamp(day).date()}:{money(value)}" for day, value in payments) if payments else "none"
            earliest_text = str(earliest.date()) if earliest is not None else ""
            if method == "full_payment" and status == "affordable_now":
                earliest_text = str(start.date())
            if method == "full_payment":
                text = f"Pay {currency} {money(payments[0][1])} today."
            elif method == "installments":
                text = f"Use the supplied installment schedule starting {payments[0][0].date()}."
            elif method == "partial_payment":
                text = f"Pay {currency} {money(payments[0][1])} today and the balance on {payments[1][0].date()}."
            elif method == "wait":
                text = f"Wait until {payments[0][0].date()} to pay {currency} {money(amount)} safely."
            else:
                text = "The request cannot be completed safely within the 90-day forecast."
            text += f" Forecast keeps at least {currency} {money(floor)} available."
            return {"request_id": request.request_id, "amount_safe_to_pay": round(safe_now, 2),
                    "affordability_status": status, "recommended_payment_method": method,
                    "payment_plan": plan, "earliest_date_for_full_payment": earliest_text,
                    "spending_changes_needed": "|".join(changes or []) or "none", "decision_explanation": text}

        # The preference rule allows full payment only if the user explicitly accepts it.
        if earliest == start and "full_payment" in accepted:
            return result("affordable_now", "full_payment", [(start, amount)])

        options = self.options[self.options.request_id.eq(request.request_id)].sort_values("payment_option_id")
        candidates: list[tuple[float, int, str, str, list[tuple[pd.Timestamp, float]]]] = []
        for _, option in options.iterrows():
            if option.payment_method != "installments" or "installments" not in accepted:
                continue
            if not pd.isna(profile.max_installment_months) and int(option.number_of_payments) > int(profile.max_installment_months):
                continue
            schedule = self.option_schedule(option)
            if schedule[-1][0] > deadline:
                continue
            if self.minimum_balance(balance, flows, start, schedule) >= floor - 0.005:
                candidates.append((float(option.total_payable_amount), len(schedule), str(option.payment_option_id), "installments", schedule))

        if (bool(request.allows_partial_payment) and "partial_payment" in accepted and 0 < safe_now < amount
                and earliest is not None and earliest <= deadline):
            schedule = [(start, safe_now), (earliest, amount - safe_now)]
            if self.minimum_balance(balance, flows, start, schedule) >= floor - 0.005:
                candidates.append((amount, len(schedule), "", "partial_payment", schedule))

        # Both partial and installment plans are ranked by the prescribed cost, start-date and
        # number-of-payments rules. A lexical option id only breaks genuine ties.
        if candidates:
            _, _, _, method, schedule = min(candidates, key=lambda c: (c[0], c[4][0][0], c[1], c[2]))
            return result("affordable_with_plan", method, schedule)

        if earliest is not None and earliest <= deadline and "full_payment" in accepted:
            return result("affordable_later", "wait", [(earliest, amount)])

        # Optional changes are considered only after all no-change plans have failed.
        changed_ids, changes = self.flexible_changes(request, flows, streams)
        if changes and "full_payment" in accepted:
            changed_flows, _ = self.baseline(request, changed_ids)
            if self.minimum_balance(balance, changed_flows, start, [(start, amount)]) >= floor - 0.005:
                return result("affordable_with_plan", "full_payment", [(start, amount)], changes)
        return result("not_affordable", "not_recommended", [])

    def run(self) -> pd.DataFrame:
        rows = [self.recommend(request) for _, request in self.requests.iterrows()]
        return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main() -> None:
    result = Forecaster().run()
    result.to_csv(ROOT / "output.csv", index=False)
    print(f"Wrote {len(result)} predictions to {ROOT / 'output.csv'}")


if __name__ == "__main__":
    main()
