
import base_agent, market_agent
_base_call = base_agent.BaseAgent.call_llm
def _mock(self, *args, **kw):
    print(f"[MOCK LLM: {self.agent_name}]", flush=True)
    return '[mock output for testing]'
base_agent.BaseAgent.call_llm = _mock
market_agent.fetch_quotes = lambda *a, **k: []
