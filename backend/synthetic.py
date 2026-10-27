"""Isolated future operational scenario fixtures. Never used by observation APIs."""
from random import Random

def operational_scenario(seed=2026,days=14):
    rng=Random(seed)
    return {'label':'ILLUSTRATIVE SCENARIO','source':'AgriOpticAI deterministic generator','seed':seed,'rows':[{'day':i,'assumed_delivery_delay_days':rng.choice([0,3,5,8])} for i in range(days)]}
