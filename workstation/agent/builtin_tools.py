import json
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web with DuckDuckGo for current information, news or documentation.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query"}},
            "required": ["query"],
        },
    },
}


USE_SKILL = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": "Read the full instructions of one of your skills before following it.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The skill's name"}},
            "required": ["name"],
        },
    },
}


def _read_skill(skills, name: str) -> dict:
    try:
        skill = skills.get(name)
    except ValueError:
        skill = None
    if not skill:
        return {"is_error": True, "text": f"There is no skill '{name}'."}
    return {"is_error": False, "text": skill.instructions}


def tool_runner(registry, skills=None):
    def run(name: str, arguments: dict) -> dict:
        if name == WEB_SEARCH["function"]["name"]:
            return web_search(arguments.get("query", ""))
        if skills and name == USE_SKILL["function"]["name"]:
            return _read_skill(skills, str(arguments.get("name", "")))
        return registry.call(name, arguments)

    return run


def web_search(query: str) -> dict:
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            soup = BeautifulSoup(response.read().decode("utf-8", errors="ignore"), "html.parser")
    except OSError as err:
        return {"is_error": True, "text": f"Search failed: {err}"}

    results = []
    for item in soup.find_all("div", class_="result__body")[:5]:
        title = item.find("a", class_="result__url")
        snippet = item.find("a", class_="result__snippet")
        if title and snippet:
            results.append({
                "title": title.get_text(strip=True),
                "link": title.get("href"),
                "snippet": snippet.get_text(strip=True),
            })
    return {"is_error": False, "text": json.dumps(results, ensure_ascii=False, indent=1)}
