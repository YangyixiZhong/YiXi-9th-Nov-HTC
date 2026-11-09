# ☕ Coffee Chat Prep: your nightly AI agent

Every evening, this agent reads tomorrow's calendar, figures out which
meetings are with people outside school, researches them, and sends you a prep brief through ntfy.
The current UTC schedule runs at 8 PM Eastern during standard time and 9 PM during daylight time.

## Setup (20 minutes): you need 4 secrets

**1. Your calendar's private link**
- **Google Calendar:** Settings → click your calendar on the left → Integrate calendar → copy **Secret address in iCal format**.
- **Outlook:** Settings → Calendar → Shared calendars → Publish a calendar → copy the **ICS** link.
- If your school account hides this option, use a personal calendar where you add your coffee chats.
- Treat this link like a password. Anyone with it can see your calendar.

**2. A Tavily key (free web search):** sign up at tavily.com and copy your API key.

**3. An ntfy topic:** In the ntfy app, subscribe to a unique topic name. Use that same topic name in the `NTFY_TOPIC` GitHub secret. The workflow publishes to `https://ntfy.sh/<topic>`, so keep the topic hard to guess and treat it like a password.

**4. Your Groq API key:** create/copy it from the [Groq Console](https://console.groq.com/keys). This project uses `openai/gpt-oss-20b` by default; it supports the function calls used by the agent.

Then:
1. Click **Use this template → Create a new repository**, and make it **private**.
2. Go to Settings → Secrets and variables → Actions, and add:
   `CALENDAR_ICS_URL`, `TAVILY_API_KEY`, `NTFY_TOPIC`, `GROQ_API_KEY`
3. Edit **`about_me.md`** with your own background. This is how the agent finds shared ground.
4. **Test it:** Actions tab → Coffee Chat Prep → Run workflow. Try `workflow`, then `agent`.

## Tips for better briefs

The agent works out who you're meeting from the calendar invite. Help it:
- Good title: **"Coffee chat – Priya Shah (Ramp, PM)"**
- Weak title: **"Chat"**
- Add their LinkedIn URL to the event description and the brief gets much better.

## Make it yours

- Change the run time: edit the `cron` line in `.github/workflows/prep.yml` (it's in UTC).
- Change the brief format or tone: edit `agent_goal()` in `agent.py`.
- Not at Cornell? Change `SCHOOL_DOMAIN` in the `env` section of `.github/workflows/prep.yml` (e.g. `wharton.upenn.edu`).
- Easiest way to change anything: paste `agent.py` into Claude and describe what you want.

## Good to know

- The agent only researches public professional info (role, team, career history), never personal life.
- Changes to your calendar can take a few hours to show up in the private link.
- To change the model, set `GROQ_MODEL` in the `env` section of `.github/workflows/prep.yml` to a model ID supported by Groq.
