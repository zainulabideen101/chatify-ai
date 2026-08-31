"""Deterministic backend commerce services: address validation, COD risk, order state machine.

These are business-critical and run entirely on the backend (never delegated to the LLM).
"""
import re
from constants import ORDER_TRANSITIONS

PLACEHOLDER_TOKENS = {"na", "n/a", "none", "-", "xxx", "test", "abc", "address", "same"}
CITY_HINTS = {
    "lahore", "karachi", "islamabad", "rawalpindi", "faisalabad", "multan",
    "peshawar", "quetta", "sialkot", "gujranwala", "hyderabad", "sargodha",
    "bahawalpur", "sukkur", "abbottabad", "gujrat", "sahiwal", "okara",
}


class AddressValidator:
    """Evaluates whether a free-text address is sufficiently deliverable.

    Does not enforce a rigid format; recognizes urban and rural patterns and
    reports specific missing components so the AI can ask a targeted question.
    """

    def validate(self, raw: str, city: str = None) -> dict:
        text = (raw or "").strip()
        lower = text.lower()
        missing = []
        issues = []

        if len(text) < 8:
            missing.append("detailed address")

        if lower in PLACEHOLDER_TOKENS or all(
            t in PLACEHOLDER_TOKENS for t in re.split(r"[\s,]+", lower) if t
        ):
            issues.append("placeholder address")

        has_number = bool(re.search(r"\d", text))
        has_street_kw = bool(re.search(
            r"\b(house|st\.?|street|gali|mohalla|block|sector|road|rd|plot|flat|floor|shop|village|chak|near)\b",
            lower,
        ))
        if not has_number and not has_street_kw:
            missing.append("house/street number")

        found_city = city or next((c for c in CITY_HINTS if c in lower), None)
        if not found_city:
            missing.append("city")

        # locality/area: at least one comma-separated segment or an area keyword
        segments = [s for s in re.split(r"[,\n]", text) if s.strip()]
        if len(segments) < 2 and not re.search(r"\b(town|colony|block|sector|scheme|phase|area)\b", lower):
            if "area/locality" not in missing:
                missing.append("area/locality")

        valid = not missing and not issues
        status = "VALID" if valid else "INCOMPLETE"
        confidence = "HIGH" if valid else ("LOW" if issues else "MEDIUM")
        return {
            "status": status,
            "valid": valid,
            "confidence": confidence,
            "missing": missing,
            "issues": issues,
            "detected_city": found_city,
        }


class CODRiskAssessor:
    """Flag-not-reject COD risk scoring from customer order history."""

    def assess(self, stats: dict) -> dict:
        delivered = stats.get("delivered_orders", 0)
        cancelled = stats.get("cancelled_orders", 0)
        refused = stats.get("refused_orders", 0)
        failed = stats.get("failed_deliveries", 0)
        total = stats.get("previous_orders", 0)
        order_value = stats.get("order_value", 0)

        score = 0
        reasons = []
        if refused > 0:
            score += refused * 25
            reasons.append(f"{refused} refused COD order(s)")
        if failed > 0:
            score += failed * 15
            reasons.append(f"{failed} failed delivery(ies)")
        if total > 0:
            cancel_rate = cancelled / max(total, 1)
            if cancel_rate > 0.3:
                score += 20
                reasons.append("high cancellation rate")
        if total == 0:
            score += 15
            reasons.append("no order history (new customer)")
        if order_value >= 15000:
            score += 15
            reasons.append("high-value COD order")
        if delivered >= 5 and refused == 0 and failed == 0:
            score -= 20
            reasons.append("strong delivery history")

        score = max(0, min(100, score))
        level = "LOW" if score < 30 else ("MEDIUM" if score < 60 else "HIGH")
        return {"score": score, "level": level, "reasons": reasons}


class OrderStateMachine:
    """Deterministic transitions for an order shopping session."""

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        return target in ORDER_TRANSITIONS.get(current, [])

    @staticmethod
    def next_states(current: str) -> list:
        return ORDER_TRANSITIONS.get(current, [])


address_validator = AddressValidator()
cod_assessor = CODRiskAssessor()
order_state_machine = OrderStateMachine()
