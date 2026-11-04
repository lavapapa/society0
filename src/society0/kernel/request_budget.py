"""激活内共享的请求额度：物理重试逐次扣减，记忆收尾独立计量。"""
from contextvars import ContextVar
from dataclasses import dataclass


class ActivationBudgetExhausted(RuntimeError):
    pass


@dataclass
class RequestBudget:
    limit: int | None
    requests: int = 0
    warning_pending: bool = False
    warning_sent: bool = False

    @property
    def remaining(self):
        return None if self.limit is None else self.limit - self.requests

    def check(self):
        if self.remaining is not None and self.remaining <= 0:
            raise ActivationBudgetExhausted('budget_exhausted')

    def take(self):
        self.check()
        self.requests += 1
        if self.remaining == 5 and not self.warning_sent:
            self.warning_pending = True


current_request_budget = ContextVar('activation_request_budget', default=None)
