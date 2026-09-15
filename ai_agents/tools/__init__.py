from ai_agents.tools.location import get_ip_location
from ai_agents.tools.rag import search_knowledge_base
from ai_agents.tools.search import web_search
from ai_agents.tools.time import get_current_time

TOOLS = [get_current_time, web_search, get_ip_location, search_knowledge_base]

__all__ = [
    "TOOLS",
    "get_current_time",
    "get_ip_location",
    "search_knowledge_base",
    "web_search",
]
