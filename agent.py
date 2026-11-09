"""
Coffee Chat Prep - a nightly AI agent (HTC AI Workshop)

Every evening this runs on GitHub Actions. It reads TOMORROW's calendar,
DECIDES which meetings are with people outside school, researches each
person's company and role, and sends you a prep brief through ntfy.

Two modes, so you can see the difference:
  MODE=workflow -> fixed steps: every event -> news on the title -> summarize.
  MODE=agent    -> AI gets a goal + tools and chooses what to do next.
"""
import json
import re
import time
import os
import sys
import xml.etree.ElementTree as XML
from datetime import datetime, timedelta

import icalendar
import recurring_ical_events
import requests
from openai import OpenAI, APIStatusError, RateLimitError
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
SCHOOL_DOMAIN = os.environ.get("SCHOOL_DOMAIN", "cornell.edu")
MAX_AGENT_STEPS = 5
MAX_WEB_SEARCHES = 2
MAX_TOOL_RESULT_CHARS = 1100
MAX_COMPLETION_TOKENS = 500


# ---------------------------------------------------------------- tools

def get_tomorrows_events():
    """Read tomorrow's timed events from your private calendar link (.ics)."""
    raw = requests.get(os.environ["CALENDAR_ICS_URL"], timeout=30)
    raw.raise_for_status()
    cal = icalendar.Calendar.from_ical(raw.content)

    start = datetime.combine(datetime.now(TZ).date() + timedelta(days=1), datetime.min.time(), TZ)
    end = start + timedelta(days=1)

    events = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        begins = ev.get("DTSTART").dt
        if not isinstance(begins, datetime):        # skip all-day events
            continue
        if begins.tzinfo is None:
            begins = begins.replace(tzinfo=TZ)
        attendees = ev.get("ATTENDEE", [])
        if not isinstance(attendees, list):
            attendees = [attendees]
        events.append({
            "_sort": begins,
            "title": str(ev.get("SUMMARY", "(no title)")),
            "time": begins.astimezone(TZ).strftime("%-I:%M %p"),
            "location": str(ev.get("LOCATION", "")),
            "description": str(ev.get("DESCRIPTION", ""))[:1000],
            "attendee_emails": [str(a).split(":", 1)[-1] for a in attendees][:10],
        })
    events.sort(key=lambda e: e.pop("_sort"))
    return {"date": start.strftime("%A %b %d"), "events": events or "No timed events tomorrow."}


def web_search(query):
    """Search the web with Tavily (free tier)."""
    r = requests.post("https://api.tavily.com/search",
                      headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
                      json={"query": query, "max_results": 3}, timeout=30)
    r.raise_for_status()
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": (x.get("content") or "")[:220]}
            for x in r.json().get("results", [])]


def company_news(company):
    """Last 30 days of headlines from Google News."""
    r = requests.get("https://news.google.com/rss/search", timeout=30,
                     params={"q": f'"{company}" when:30d', "hl": "en-US", "gl": "US", "ceid": "US:en"})
    r.raise_for_status()
    items = [{"title": i.findtext("title"), "published": i.findtext("pubDate")}
             for i in XML.fromstring(r.content).iter("item")]
    return {"company": company, "headlines": items[:3] or "No recent news found."}


TOOLS = [
    {"type": "function", "function": {
        "name": "get_tomorrows_events",
        "description": "Tomorrow's calendar: title, time, location, description and attendee emails.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Use for a person's professional role or to identify an unclear company.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "company_news",
        "description": "Recent news headlines about a company (last 30 days).",
        "parameters": {"type": "object", "properties": {"company": {"type": "string"}}, "required": ["company"]}}},
]


def run_tool(name, args):
    try:
        if name == "get_tomorrows_events":
            return get_tomorrows_events()
        if name == "web_search":
            return web_search(args["query"])
        if name == "company_news":
            return company_news(args["company"])
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------- workflow vs agent

def agent_goal(profile):
    return f"""You are my networking prep agent. Every evening you prepare me for tomorrow.

GOAL: For every meeting tomorrow with someone OUTSIDE my school (coffee chats, alumni,
recruiters, interviews), give me a short prep brief.

You decide what to do. Guidelines:
- Start by reading tomorrow's calendar.
- Skip classes, study groups, club meetings, and meetings where everyone has an @{SCHOOL_DOMAIN} email.
  If you're unsure whether a meeting is external, prep it.
- Work out the person and company from the title, description and emails
  (an email like priya@ramp.com means the company is Ramp). If unclear, search.
- Research the company's recent news, and the person's PROFESSIONAL background only
  (title, team, past companies). Never include anything about their personal life, family or home.
- Find genuine shared ground with me using my profile below. Never invent any.
- If you can't identify someone, say so. Don't guess.
- Never confuse MY PROFILE with the meeting attendee; do not search for me as the attendee.
- Use at most two distinct web searches TOTAL, never repeat or rephrase a search.
- After the calendar and limited research, finish the brief; do not keep searching.

Format per meeting (plain text for ntfy, under 700 characters each):
Name, Title at Company (time)
• What's new: 1-2 recent company developments
• Ask: 2 smart questions tied to that news and their role
• Shared ground: ...
• Your ask: one small, specific ask to end the chat with
Then one line: "Skipped: ..." listing meetings you decided didn't need prep, and why.

If nothing needs prep, reply only: No external meetings tomorrow. Enjoy the night off.

MY PROFILE:
{profile}"""


def run_workflow(client, profile):
    """Fixed recipe: every event gets the same treatment. The AI decides nothing."""
    cal = get_tomorrows_events()
    events = cal["events"] if isinstance(cal["events"], list) else []
    trail = ["get_tomorrows_events"]
    notes = []
    for ev in events:
        trail.append(f"company_news({ev['title']})")
        notes.append({"meeting": ev, "news": company_news(ev["title"])})
    resp = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content":
        "Write a short prep note for each of these meetings using the news found. "
        f"Plain text, under 1,500 characters total.\n{json.dumps(notes, default=str)}"}])
    return resp.choices[0].message.content, trail


def _compact(value, limit=MAX_TOOL_RESULT_CHARS):
    """Keep tool outputs small so prior searches do not inflate every request."""
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= limit:
        return serialized
    return json.dumps({"summary": serialized[:limit], "truncated": True}, ensure_ascii=False)


def _complete(client, messages, tools=None):
    """Use bounded outputs; retry a temporary TPM rejection once."""
    kwargs = {"model": MODEL, "messages": messages, "max_tokens": MAX_COMPLETION_TOKENS}
    if tools is not None:
        kwargs["tools"] = tools
    try:
        return client.chat.completions.create(**kwargs).choices[0].message
    except (RateLimitError, APIStatusError) as exc:
        # 413 is also used for Groq TPM overages. Waiting for the minute to
        # reset is useful, but compacting context is still essential.
        if getattr(exc, "status_code", None) not in (413, 429):
            raise
        print("Groq rate/token limit reached; waiting 65 seconds before retry.", flush=True)
        time.sleep(65)
        return client.chat.completions.create(**kwargs).choices[0].message


def run_agent(client, profile):
    """Bounded tool-use agent with duplicate-search protection and a final brief."""
    now = datetime.now(TZ).strftime("%A %b %d, %-I:%M %p")
    goal = agent_goal(profile)
    messages = [{"role": "system", "content": goal},
                {"role": "user", "content": f"It's {now}. Prep me for tomorrow."}]
    trail = []
    search_count = 0
    searched = set()
    calendar_result = None
    research_notes = []
    for step in range(MAX_AGENT_STEPS):
        # Stop offering web search after the cap. Other tools remain available.
        available = [tool for tool in TOOLS
                     if search_count < MAX_WEB_SEARCHES or tool["function"]["name"] != "web_search"]
        msg = _complete(client, messages, tools=available)
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or "No brief returned.", trail
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            label = f"{name}({', '.join(str(v) for v in args.values())})"
            print(f"Agent step {step + 1}: {label}", flush=True)
            trail.append(label)
            if name == "web_search":
                query = str(args.get("query", "")).strip()
                # Word-set signature catches near-identical query rephrasings.
                signature = frozenset(re.findall(r"[a-z0-9]+", query.lower()))
                if not query or search_count >= MAX_WEB_SEARCHES:
                    result = {"error": "Web search budget exhausted. Finish the brief."}
                elif any(signature == old or (signature and old and
                         len(signature & old) / len(signature | old) >= 0.65) for old in searched):
                    result = {"error": "Similar search already performed. Use existing evidence."}
                else:
                    searched.add(signature)
                    search_count += 1
                    result = run_tool(name, args)
            else:
                result = run_tool(name, args)
            if name == "get_tomorrows_events":
                calendar_result = result
            elif name in ("web_search", "company_news"):
                research_notes.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": _compact(result)})

    # Do not pass the growing tool-call transcript into the final request.
    # Build a short standalone prompt so a long search history cannot cause 413.
    final_messages = [
        {"role": "system", "content": goal},
        {"role": "user", "content":
         "Write the final networking briefs now. No more tools or searches. "
         "Use only the evidence below; label unknowns, never invent details.\n"
         f"Calendar: {_compact(calendar_result, 3500)}\n"
         f"Research: {_compact(research_notes, 3500)}"},
    ]
    return _complete(client, final_messages).content or "No brief returned.", trail


# ---------------------------------------------------------------- delivery

def post_to_ntfy(text):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("\n(No NTFY_TOPIC set - printing instead)\n")
        print(text)
        return
    url = f"https://ntfy.sh/{topic}"
    remaining = text.encode("utf-8")
    while remaining:
        end = min(3500, len(remaining))
        while True:
            try:
                message = remaining[:end].decode("utf-8")
                break
            except UnicodeDecodeError as exc:
                end = exc.start
        requests.post(
            url,
            data=message.encode("utf-8"),
            headers={"Title": "Coffee Chat Prep", "Tags": "coffee"},
            timeout=30,
        ).raise_for_status()
        remaining = remaining[end:]


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "agent")).lower()
    with open("about_me.md") as f:
        profile = f.read()
    client = OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )

    brief, trail = (run_workflow if mode == "workflow" else run_agent)(client, profile)

    tomorrow = datetime.now(TZ) + timedelta(days=1)
    header = f"☕ Coffee Chat Prep ({mode}) - {tomorrow:%a %b %d}\n\n"
    footer = f"\n\n🧭 Path it took: {' → '.join(trail) if trail else '(none)'}"
    post_to_ntfy(header + (brief or "(no brief returned)") + footer)


if __name__ == "__main__":
    main()
