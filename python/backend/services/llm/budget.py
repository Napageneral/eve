"""
Budget management for beta users using LiteLLM SDK
"""
import litellm
from datetime import datetime
from typing import Dict
import logging

logger = logging.getLogger(__name__)

class BetaBudgetManager:
    """Simple budget tracking using LiteLLM SDK for beta users"""
    
    def __init__(self, monthly_limit: float = 10.0):
        self.user_budgets: Dict[str, Dict] = {}
        self.monthly_limit = monthly_limit
    
    def check_budget(self, user_id: str) -> bool:
        """Check if user has budget remaining"""
        user_budget = self.user_budgets.get(user_id, {
            "spent": 0.0,
            "month": datetime.now().month
        })
        
        # Reset monthly
        if user_budget["month"] != datetime.now().month:
            user_budget = {"spent": 0.0, "month": datetime.now().month}
            self.user_budgets[user_id] = user_budget
        
        return user_budget["spent"] < self.monthly_limit
    
    def track_usage(self, user_id: str, cost: float):
        """Track usage after completion"""
        if user_id not in self.user_budgets:
            self.user_budgets[user_id] = {
                "spent": 0.0,
                "month": datetime.now().month
            }
        
        self.user_budgets[user_id]["spent"] += cost
        
        # Log warning if approaching limit
        if self.user_budgets[user_id]["spent"] > self.monthly_limit * 0.8:
            logger.warning(f"User {user_id} at 80% of monthly budget")
    
    def get_usage(self, user_id: str) -> Dict:
        """Get current usage for a user"""
        user_budget = self.user_budgets.get(user_id, {
            "spent": 0.0,
            "month": datetime.now().month
        })
        
        # Reset if new month
        if user_budget["month"] != datetime.now().month:
            return {"spent": 0.0, "remaining": self.monthly_limit}
        
        return {
            "spent": user_budget["spent"],
            "remaining": max(0, self.monthly_limit - user_budget["spent"])
        }

# Global instance
budget_manager = BetaBudgetManager()

def get_completion_with_budget(compiled_prompt, user_id: str, **kwargs):
    """Wrapper for get_completion with budget checking"""
    from .completions import get_completion
    
    if not budget_manager.check_budget(user_id):
        raise ValueError("Monthly budget exceeded")
    
    response = get_completion(compiled_prompt, **kwargs)
    
    # Track usage if successful
    if "usage" in response and "total_cost" in response["usage"]:
        budget_manager.track_usage(user_id, response["usage"]["total_cost"])
    
    return response 