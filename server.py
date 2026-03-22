#!/usr/bin/env python3
"""MovieSeats web server — conversational seat finder."""

import asyncio
import json
import logging
import time
import datetime
from pathlib import Path

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from movieseats.fetcher.theaters import find_theaters_and_showtimes
from movieseats.fetcher.seats import fetch_all_seat_maps
from movieseats.fetcher.browse import browse_movies_near
from movieseats.seats.scorer import find_best_seats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

app = FastAPI()

# Firestore for search logging
try:
    from google.cloud import firestore
    db = firestore.AsyncClient(project="movieseats-app")
    logger.info("Firestore connected")
except Exception as e:
    db = None
    logger.warning("Firestore not available (running locally?): %s", str(e)[:50])


async def log_search(data: dict):
    """Log a search event to Firestore."""
    if not db:
        return
    try:
        data["timestamp"] = firestore.SERVER_TIMESTAMP
        await db.collection("searches").add(data)
    except Exception as e:
        logger.warning("Firestore log failed: %s", str(e)[:50])

# In-memory session store (simple — one user at a time)
sessions: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "web" / "index.html"
    return HTMLResponse(html_path.read_text())


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")

    # Get or create session
    if session_id not in sessions:
        sessions[session_id] = {
            "history": [],
            "last_search": None,  # last successful search params
            "last_results": None,  # last results for follow-ups
        }
    session = sessions[session_id]

    async def stream():
        yield _sse("status", "Thinking...")

        # Step 1: Parse intent with conversation context
        parsed = await _parse_intent(message, session)

        if not parsed:
            yield _sse("error", "I couldn't understand that. Try something like:\n\"Best seats for Dhurandhar tomorrow evening near 75035, 2 tickets\"")
            return

        action = parsed.get("action", "search")

        # Handle non-search actions
        if action == "chat":
            yield _sse("chat_response", parsed.get("response", ""))
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": parsed.get("response", "")})
            return

        if action == "browse":
            zipcode = parsed.get("zipcode", "")
            if not zipcode:
                yield _sse("chat_response", "What's your zipcode? I'll show you what's playing nearby.")
                return

            yield _sse("status", f"Checking what's playing near {zipcode}...")
            session["history"].append({"role": "user", "content": message})
            session["last_search"] = parsed

            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                )
                movies = await browse_movies_near(context, zipcode)
                await browser.close()

            if not movies:
                yield _sse("error", f"Couldn't find movies near {zipcode}. Try a different zipcode.")
                return

            # Remove status
            yield _sse("movies", json.dumps(movies))

            movie_list = "\n".join(f"- {m['name']}" for m in movies[:15])
            response_text = f"Here are the movies playing near {zipcode}. Which one do you want to see? I'll find you the best seats."
            yield _sse("chat_response", response_text)

            session["history"].append({"role": "assistant", "content": f"Found {len(movies)} movies. {response_text}"})

            await log_search({
                "type": "browse",
                "zipcode": zipcode,
                "movies_found": len(movies),
                "message": message,
            })
            return

        if action == "search":
            movie = parsed.get("movie", "")
            zipcode = parsed.get("zipcode", "")
            date = parsed.get("date", "")
            time_pref = parsed.get("time_pref", "evening")
            num_seats = parsed.get("seats", 2)

            if not movie or not zipcode:
                yield _sse("chat_response", parsed.get("response", "I need a movie name and zipcode to search. What movie are you looking for and what's your zipcode?"))
                return

            # Build display date
            display_date = "today"
            if date:
                try:
                    today = datetime.date.today()
                    month = today.month
                    target = datetime.date(today.year, month, int(date))
                    display_date = target.strftime("%A, %B %d")
                except Exception:
                    display_date = f"the {date}th"

            yield _sse("parsed", json.dumps(parsed))
            yield _sse("status", f"Searching for {movie} near {zipcode} for {display_date}...")

            # Save search params
            session["last_search"] = parsed
            session["history"].append({"role": "user", "content": message})

            # Step 2: Find theaters
            start = time.time()
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                )

                date_text = date
                if date_text and "/" not in date_text:
                    month = datetime.date.today().month
                    date_text = f"{month}/{date_text}"

                theaters = await find_theaters_and_showtimes(
                    context, zipcode, movie, date_text=date_text, time_pref=time_pref,
                )

                if not theaters:
                    yield _sse("error", f"No theaters found showing {movie} near {zipcode}. Check the movie name or try a different zipcode.")
                    await browser.close()
                    return

                total_st = sum(len(t.showtimes) for t in theaters)
                yield _sse("status", f"Found {len(theaters)} theaters, {total_st} showtimes for {display_date}. Fetching seat maps...")

                seat_data = await fetch_all_seat_maps(theaters, context)
                await browser.close()

            if not seat_data:
                yield _sse("error", "Could not fetch seat maps. Please try again.")
                return

            elapsed = time.time() - start
            failed = total_st - len(seat_data)
            fail_msg = f" ({failed} showtimes couldn't load)" if failed > 0 else ""
            yield _sse("status", f"Scanned {len(seat_data)} showtimes for {display_date} in {elapsed:.0f}s{fail_msg}. Analyzing...")

            # Step 4: Score and build results
            all_results = []
            for showtime, seat_map in seat_data:
                total = sum(len(r) for r in seat_map.rows)
                avail = sum(1 for r in seat_map.rows for s in r if s.status == "available")
                recs = find_best_seats(seat_map, showtime, num_seats, top_n=2)

                theater_result = {
                    "theater": showtime.theater_name,
                    "time": showtime.time,
                    "format": showtime.format,
                    "price": showtime.price,
                    "available": avail,
                    "total": total,
                    "url": showtime.url,
                    "seats": [],
                }

                for rec in recs:
                    seat_labels = ", ".join(f"{s.row}{s.number}" for s in rec.seats)
                    theater_result["seats"].append({
                        "labels": seat_labels,
                        "score": rec.score,
                        "reasoning": rec.reasoning,
                    })

                all_results.append(theater_result)

            # Sort by format preference + score
            format_pref = parsed.get("format_pref", "any")

            def _sort_key(r):
                score = r["seats"][0]["score"] if r["seats"] else 0
                fmt = r["format"].lower()
                price = r.get("price", 0)

                if format_pref == "cheapest":
                    # Cheapest first, then by score
                    return (-price if price else 0, score)
                elif format_pref == "imax":
                    bonus = 1.0 if "imax" in fmt else 0
                    return (bonus, score)
                elif format_pref == "xd":
                    bonus = 1.0 if "xd" in fmt else 0
                    return (bonus, score)
                elif format_pref == "standard":
                    bonus = 1.0 if fmt == "standard" else 0
                    return (bonus, score)
                else:  # "any"
                    return (score,)

            all_results.sort(key=_sort_key, reverse=True)

            session["last_results"] = all_results

            yield _sse("results", json.dumps(all_results))

            # Step 5: AI recommendation
            yield _sse("status", "Getting AI recommendation...")
            ai_text = await _get_ai_recommendation(all_results, movie, num_seats)
            yield _sse("recommendation", ai_text)
            yield _sse("done", json.dumps({"elapsed": round(elapsed, 1)}))

            session["history"].append({"role": "assistant", "content": f"Found {len(all_results)} showtimes. {ai_text}"})

            # Log to Firestore
            best = all_results[0] if all_results else {}
            best_seats = best.get("seats", [{}])[0] if best.get("seats") else {}
            await log_search({
                "type": "search",
                "movie": movie,
                "zipcode": zipcode,
                "date": date,
                "time_pref": time_pref,
                "format_pref": format_pref,
                "num_seats": num_seats,
                "theaters_found": len(seat_data),
                "best_theater": best.get("theater", ""),
                "best_seats": best_seats.get("labels", ""),
                "best_score": best_seats.get("score", 0),
                "elapsed": round(elapsed, 1),
                "message": message,
            })

    return StreamingResponse(stream(), media_type="text/event-stream")


INTENT_SYSTEM = """You are the intent parser for a movie seat finder app. Your job is to understand what the user wants and extract structured data.

You have access to the conversation history to understand follow-up questions.

Today is {today}.

Return ONLY a JSON object with these fields:

For a SEARCH (finding best seats for a specific movie):
{{
  "action": "search",
  "movie": "movie name",
  "zipcode": "5-digit US zipcode",
  "date": "day number like 22, or empty for today",
  "time_pref": "morning" | "afternoon" | "evening" | "all",
  "seats": 2,
  "format_pref": "any" | "imax" | "xd" | "standard" | "cheapest"
}}

For BROWSING movies (user wants to see what's playing):
{{
  "action": "browse",
  "zipcode": "5-digit US zipcode"
}}

For a CHAT response (greeting, question, clarification):
{{
  "action": "chat",
  "response": "your helpful response"
}}

IMPORTANT RULES:
- If the user asks a follow-up like "how about morning?" or "check Monday", merge with the previous search params.
- If info is missing (no zipcode, no movie), ask for it in a chat response.
- "tomorrow" = {tomorrow_day}, "today" = {today_day}
- Days of the week: calculate the actual date number.
- Default seats = 2 if not specified.
- Default time_pref = "all" if not specified.
- Understand casual language: "3 tickets", "for three", "me and my friends" = figure out seat count.
- "near Plano" or "in Frisco" = help user but ask for zipcode if not given. Common ones: Plano=75024, Frisco=75035, Allen=75013, McKinney=75070, Dallas=75201.
- If user says "what's playing", "any movies", "what movies are showing", "show me movies" → use "browse" action.
- If user picks a movie from the browse list (like just typing a movie name after browsing) → use "search" with that movie + previous zipcode.
- If user says "IMAX", "XD", "premium" → set format_pref to "imax" or "xd".
- If user says "cheapest", "budget", "standard only" → set format_pref to "cheapest".
- Default format_pref is "any".
"""


async def _parse_intent(message: str, session: dict) -> dict | None:
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    system = INTENT_SYSTEM.format(
        today=today.strftime("%A, %B %d, %Y"),
        tomorrow_day=tomorrow.day,
        today_day=today.day,
    )

    # Build messages with history for context
    messages = []

    # Include recent history (last 6 exchanges)
    for h in session.get("history", [])[-6:]:
        messages.append(h)

    # Include last search params as context
    last = session.get("last_search")
    if last:
        context = f"[Previous search: movie={last.get('movie')}, zipcode={last.get('zipcode')}, date={last.get('date')}, time={last.get('time_pref')}, seats={last.get('seats')}]"
        messages.append({"role": "user", "content": context})
        messages.append({"role": "assistant", "content": "Understood, I have the previous search context."})

    messages.append({"role": "user", "content": message})

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=system,
            messages=messages,
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```json")[-1].split("```")[0].strip() if "```json" in text else text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except Exception as e:
        logger.error("Intent parsing failed: %s", e)
        return None


async def _get_ai_recommendation(results: list, movie: str, num_seats: int) -> str:
    summary = ""
    for r in results[:8]:
        best = r["seats"][0] if r["seats"] else None
        summary += f"- {r['theater']} | {r['time']} | {r['format']} | {r['available']}/{r['total']} available"
        if best:
            summary += f" | Best: {best['labels']} (score {best['score']:.2f})"
        summary += "\n"

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=250,
            messages=[{"role": "user", "content": f"""You're a friendly movie seat advisor. Based on this data for "{movie}" ({num_seats} seats):

{summary}

Give a brief, confident recommendation in 2-3 short sentences. Mention the specific seats and why. If a theater is nearly sold out, warn about it. Be casual and helpful, like texting a friend."""}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        if results and results[0]["seats"]:
            best = results[0]
            return f"Best option: {best['seats'][0]['labels']} at {best['theater']}, {best['time']}."
        return "Check the results above for available options."


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
