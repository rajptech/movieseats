#!/usr/bin/env python3
"""MovieSeats web server — AI-powered seat finder with web search."""

import asyncio
import json
import logging
import time
import datetime
from pathlib import Path

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from config import ANTHROPIC_API_KEY, MODEL_FAST, MODEL_SMART
from movieseats.fetcher.theaters import find_theaters_and_showtimes
from movieseats.fetcher.seats import fetch_all_seat_maps
# from movieseats.fetcher.browse import browse_movies_near  # disabled for now
from movieseats.fetcher.india import find_india_theaters, fetch_india_seat_map, INDIA_CITIES
from movieseats.seats.scorer import find_best_seats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS — allow Firebase Hosting to call Cloud Run API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://movieseats-app.web.app",
        "https://movieseats-app.firebaseapp.com",
        "https://movieseats.org",
        "https://www.movieseats.org",
        "http://localhost:8000",
        "http://localhost:5000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Firestore
try:
    from google.cloud import firestore
    db = firestore.AsyncClient(project="movieseats-app")
    logger.info("Firestore connected")
except Exception as e:
    db = None
    logger.warning("Firestore not available: %s", str(e)[:50])

# Sessions
sessions: dict[str, dict] = {}


async def log_search(data: dict):
    if not db:
        return
    try:
        data["timestamp"] = firestore.SERVER_TIMESTAMP
        await db.collection("searches").add(data)
    except Exception as e:
        logger.warning("Firestore log failed: %s", str(e)[:50])


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "web" / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/results/{session_id}")
async def get_results(session_id: str):
    """Fallback endpoint — get results if SSE stream broke."""
    if session_id not in sessions:
        return {"status": "no_session"}
    session = sessions[session_id]
    last_results = session.get("last_results")
    if last_results:
        return {
            "status": "done",
            "results": last_results.get("results", []),
            "recommendation": last_results.get("recommendation", ""),
        }
    return {"status": "pending"}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")

    if session_id not in sessions:
        sessions[session_id] = {"history": [], "last_search": None}
    session = sessions[session_id]

    async def stream():
        yield _sse("status", "Thinking...")

        # Step 1: Parse intent with Haiku (cheap, fast)
        parsed = await _parse_intent(message, session)
        if not parsed:
            yield _sse("chat_response", "Tell me the movie name and your zipcode or city, and I'll find the best seats for you! For example: \"Dhurandhar 75035\" or \"Hoppers in Irvine\"")
            return

        action = parsed.get("action", "search")

        # Log non-search interactions here (searches log when they complete with full results)
        if action != "search":
            await log_search({
                "type": action,
                "message": message,
                "movie": parsed.get("movie", ""),
                "zipcode": parsed.get("zipcode", ""),
                "session_id": session_id,
                "response": parsed.get("response", "")[:200] if action in ("chat", "need_zipcode") else "",
            })

        if action == "chat":
            yield _sse("chat_response", parsed.get("response", ""))
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": parsed.get("response", "")})
            return

        if action == "need_zipcode":
            yield _sse("chat_response", parsed.get("response", "What's your zipcode or city? I need it to find theaters near you."))
            session["last_search"] = parsed
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": parsed["response"]})
            return

        if action == "info":
            query = parsed.get("query", message)
            query_lower = query.lower()
            zipcode = parsed.get("zipcode", "")
            location = parsed.get("location_name", "")

            # Redirect "what's playing" type questions to seat finding
            if any(phrase in query_lower for phrase in ["what's playing", "whats playing", "what is playing", "movies near", "what movies", "what's showing", "whats showing"]):
                yield _sse("chat_response", "I can find the best seats for any movie! Just tell me the movie name and your zipcode or city. For example: \"Dhurandhar 75035\" or \"Hoppers in Irvine\"")
                session["history"].append({"role": "user", "content": message})
                session["history"].append({"role": "assistant", "content": "Tell me the movie name and location to find seats."})
                return

            yield _sse("status", "Looking that up...")

            try:
                response = await client.messages.create(
                    model=MODEL_SMART,
                    max_tokens=600,
                    tools=[{
                        "type": "web_search_20260209",
                        "name": "web_search",
                        "max_uses": 3,
                    }],
                    messages=[{"role": "user", "content": f"""Answer this question about movies/theaters. Be helpful and concise (3-5 sentences max).

IMPORTANT RULES:
- Do NOT use markdown links like [text](url). Just write plain text.
- Do NOT say "check Fandango" or redirect to other websites. YOU are the movie expert.
- If asking what's playing, search and list actual movie names currently in theaters.
- If asking about a genre, search and list specific movies of that genre in theaters now.
- If asking about an actor, search for their current movies in theaters.
- After listing movies, say: "Want me to find the best seats? Just type the movie name!"
- If asking about a specific theater, search for its showtimes and movies.
- If asking about pricing or logistics, give a direct answer.

Location context: {location or zipcode or 'not specified'}

Question: {query}"""}],
                )

                answer = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        answer = block.text.strip()

                if not answer:
                    answer = "I couldn't find that information. Try asking about a specific movie with your zipcode!"

            except Exception as e:
                logger.error("Info query failed: %s", e)
                answer = "I had trouble looking that up. Try asking about a specific movie with your zipcode!"

            yield _sse("chat_response", answer)
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": answer})
            return

        # Browse feature disabled — handled by info action now
        if action == "browse":
            yield _sse("chat_response", "Tell me the movie name and your zipcode or city, and I'll find the best seats for you!")
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": "Tell me the movie name and your zipcode or city, and I'll find the best seats for you!"})
            return

        if action == "search":
            movie_raw = parsed.get("movie", "")
            zipcode = parsed.get("zipcode", "")
            date = parsed.get("date", "")
            time_pref = parsed.get("time_pref", "evening")
            num_seats = parsed.get("seats", 2)
            format_pref = parsed.get("format_pref", "any")
            seat_pref = parsed.get("seat_pref", "best")
            country = parsed.get("country", "us")

            location_name = parsed.get("location_name", "")

            # India: need city name, not zipcode
            if country == "india":
                if not location_name:
                    yield _sse("chat_response", f"I'd love to find seats for {movie_raw}! Which city in India are you in?")
                    session["last_search"] = parsed
                    session["history"].append({"role": "user", "content": message})
                    return
            else:
                if not zipcode:
                    yield _sse("chat_response", f"I'd love to find seats for {movie_raw}! What's your zipcode or city?")
                    session["last_search"] = parsed
                    session["history"].append({"role": "user", "content": message})
                    return

            if not movie_raw:
                yield _sse("chat_response", "What movie are you looking for?")
                return

            # Step 2: Web search with Sonnet 4.6 to get correct movie name
            location_display = location_name or zipcode
            yield _sse("status", f"Looking up {movie_raw} near {location_display}...")

            movie_info = await _web_search_movie(movie_raw, zipcode)

            correct_movie = movie_info.get("correct_name", movie_raw)
            search_slug = movie_info.get("cinemark_search", movie_raw.lower().replace(" ", "-"))

            logger.info("Web search: '%s' → '%s' (slug: %s)", movie_raw, correct_movie, search_slug)

            # Build display date
            display_date = "today"
            if date:
                try:
                    today = datetime.date.today()
                    target = datetime.date(today.year, today.month, int(date))
                    display_date = target.strftime("%A, %B %d")
                except Exception:
                    display_date = f"the {date}th"

            yield _sse("status", f"Searching for {correct_movie} near {location_display} for {display_date}...")

            # Save search
            session["last_search"] = {**parsed, "movie": correct_movie, "search_slug": search_slug}
            session["history"].append({"role": "user", "content": message})

            # Step 3: Find theaters and seat maps
            start = time.time()
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1366, "height": 768},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

                seat_data = []

                if country == "india":
                    # --- INDIA PATH (District.in) ---
                    india_city = location_name or "hyderabad"
                    india_results = await find_india_theaters(context, india_city, correct_movie)

                    if not india_results:
                        yield _sse("chat_response", f"I couldn't find {correct_movie} in theaters near {india_city}. The movie might not be playing in your city yet, or try a different city name.")
                        session["history"].append({"role": "user", "content": message})
                        session["history"].append({"role": "assistant", "content": f"No theaters found showing {correct_movie} near {india_city}."})
                        await browser.close()
                        return

                    total_st = sum(len(sts) for _, sts in india_results)

                    for theater_name, showtimes in india_results:
                        yield _sse("theater_progress", json.dumps({"name": theater_name, "showtimes": len(showtimes)}))

                    yield _sse("status", f"Checking seats across {total_st} showtimes...")

                    for theater_name, showtimes in india_results:
                        for st in showtimes:
                            result = await fetch_india_seat_map(context, st, correct_movie, india_city)
                            if result:
                                seat_data.append(result)

                else:
                    # --- US PATH (Cinemark) ---
                    date_text = date
                    if date_text and "/" not in date_text:
                        month = datetime.date.today().month
                        date_text = f"{month}/{date_text}"

                    theaters = await find_theaters_and_showtimes(
                        context, zipcode, correct_movie, date_text=date_text, time_pref=time_pref,
                    )

                    if not theaters:
                        yield _sse("chat_response", f"I couldn't find {correct_movie} at theaters near {location_display}. The movie might not be playing in your area, or showtimes haven't been posted yet. Try a different zipcode or check back later!")
                        session["history"].append({"role": "user", "content": message})
                        session["history"].append({"role": "assistant", "content": f"No theaters found showing {correct_movie} near {location_display}."})
                        await browser.close()
                        return

                    total_st = sum(len(t.showtimes) for t in theaters)

                    for t in theaters:
                        yield _sse("theater_progress", json.dumps({"name": t.name, "showtimes": len(t.showtimes)}))

                    yield _sse("status", f"Checking seats across {total_st} showtimes...")

                    seat_data = await fetch_all_seat_maps(theaters, context)

                await browser.close()

            if not seat_data:
                yield _sse("error", "Could not load seat maps. Please try again.")
                return

            elapsed = time.time() - start

            # Step 4: Score seats (math) and build results
            all_results = []
            for showtime, seat_map in seat_data:
                total = sum(len(r) for r in seat_map.rows)
                avail = sum(1 for r in seat_map.rows for s in r if s.status == "available")
                recs = find_best_seats(seat_map, showtime, num_seats, top_n=2)

                show_date = ""
                if showtime.date:
                    try:
                        dt = datetime.date.fromisoformat(showtime.date)
                        show_date = dt.strftime("%a %b %d")
                    except Exception:
                        show_date = showtime.date

                theater_result = {
                    "theater": showtime.theater_name,
                    "time": showtime.time,
                    "date": show_date or display_date,
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

            # Step 5: AI ranking with Haiku — let AI pick the best
            yield _sse("status", f"Scanned {len(all_results)} showtimes in {elapsed:.0f}s. AI picking best seats...")

            ranked_results, ai_recommendation = await _ai_rank_and_recommend(
                all_results, correct_movie, num_seats, format_pref, zipcode, seat_pref
            )

            # Save results to session so fallback endpoint can serve them
            session["last_results"] = {
                "results": ranked_results,
                "recommendation": ai_recommendation,
            }

            yield _sse("results", json.dumps(ranked_results))
            yield _sse("recommendation", ai_recommendation)
            yield _sse("done", json.dumps({"elapsed": round(elapsed, 1)}))

            # Save EVERYTHING to history — full results so follow-ups have complete context
            full_response = f"Search: {correct_movie} near {zipcode}, {display_date}, {time_pref}, {num_seats} seats, format={format_pref}\n\nResults:\n"
            for r in ranked_results:
                best = r["seats"][0] if r["seats"] else {}
                full_response += f"- {r['theater']} | {r.get('date','')} {r['time']} | {r['format']} | ${r.get('price',0):.2f} | {r['available']}/{r['total']} seats | Best: {best.get('labels','sold out')} (score {best.get('score',0):.2f})\n"
            full_response += f"\nRecommendation: {ai_recommendation}"
            session["history"].append({"role": "assistant", "content": full_response})

            best = ranked_results[0] if ranked_results else {}
            best_seats = best.get("seats", [{}])[0] if best.get("seats") else {}
            await log_search({
                "type": "search", "movie": correct_movie, "movie_raw": movie_raw,
                "zipcode": zipcode, "date": date, "time_pref": time_pref,
                "format_pref": format_pref, "num_seats": num_seats,
                "theaters_found": len(all_results),
                "best_theater": best.get("theater", ""),
                "best_seats": best_seats.get("labels", ""),
                "elapsed": round(elapsed, 1), "message": message,
            })

    return StreamingResponse(stream(), media_type="text/event-stream")


# --- Intent Parsing (Haiku 4.5 + full history + prompt caching) ---

INTENT_SYSTEM = """You parse user messages for a movie seat finder app. Today is {today}.

Return JSON only. There are 3 possible actions:

ACTION 1 - SEARCH (find best seats for a movie):
{{"action":"search","movie":"movie name AS TYPED","zipcode":"5-digit zip or empty","location_name":"city/area name","country":"us|india","date":"day number or empty","time_pref":"morning|afternoon|evening|all","seats":2,"format_pref":"any|imax|xd|standard|cheapest|dolby","seat_pref":"best|back|front|aisle|center"}}

ACTION 2 - INFO (answer a question using web search — about movies, theaters, genres, actors, logistics, pricing):
{{"action":"info","query":"the user's question to research","zipcode":"zip if mentioned","location_name":"city if mentioned"}}

ACTION 3 - NEED ZIPCODE (movie given but no location):
{{"action":"need_zipcode","response":"friendly message asking for zipcode or city","movie":"movie name"}}

ROUTING RULES:
- "Dhurandhar 75035" → search (movie + location = find seats)
- "Dhurandhar" alone → need_zipcode
- "What's playing near 75035?" → info (browse movies)
- "Any action movies near Frisco?" → info (genre search)
- "What Sunny Deol movies are playing?" → info (actor search)
- "What's showing at Cinemark Frisco tonight?" → info (theater search)
- "Any 9 PM shows near 75035?" → info (time search)
- "How much are tickets for Dhurandhar?" → info (pricing, include zipcode if in conversation)
- "Where is Cinemark West Plano?" → info (logistics)
- "Is Dhurandhar still in theaters?" → info
- "Compare 7 PM vs 9 PM" → use previous search context, do a search with time_pref="all"
- "Back row seats for Dhurandhar 75035" → search with seat_pref="back"
- "Aisle seats" → search with seat_pref="aisle" (use previous movie/zip)
- "Cheapest option" → search with format_pref="cheapest"
- "IMAX showing" → search with format_pref="imax"
- "Hi", "thanks", greetings → info with friendly response query

IMPORTANT RULES:
- "tomorrow" = day {tomorrow_day}, "today" = day {today_day}
- Default: seats=2, time_pref="evening", format_pref="any", seat_pref="best"
- DO NOT correct movie spelling — return exactly what user typed
- ANY 5-digit number is a valid US zipcode. NEVER reject a zipcode.
- CITY NAMES → convert to zipcode. Common US: Frisco=75035, Plano=75024, Allen=75013, McKinney=75070, Dallas=75201, Irvine=92614, LA=90001, NYC=10001, Chicago=60601, Houston=77001, SF=94102, Dublin CA=94568, Sunnyvale=94086.
- INDIA: If user mentions an Indian city (Hyderabad, Mumbai, Delhi, Bangalore, Chennai, Kolkata, Pune, etc) or uses a 6-digit pincode, set country="india" and location_name to the city name. Leave zipcode empty for India.
- Indian cities: Hyderabad, Mumbai, Delhi, Bangalore/Bengaluru, Chennai, Kolkata, Pune, Ahmedabad, Jaipur, Lucknow, Kochi, Chandigarh, Noida, Gurgaon/Gurugram, Vizag, Indore, Bhopal, Secunderabad, Gachibowli.
- Default country="us" unless Indian city/pincode is detected.
- "near me" with NO city/zipcode → need_zipcode
- Follow-ups: use previous movie/zipcode from conversation history
- "how about morning" / "check Monday" / "3 tickets" → search, keep previous context, change only what they asked"""


async def _parse_intent(message: str, session: dict) -> dict | None:
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    system_text = INTENT_SYSTEM.format(
        today=today.strftime('%A, %B %d, %Y'),
        tomorrow_day=tomorrow.day,
        today_day=today.day,
    )

    # Build messages: full conversation history + new message
    messages = []
    for h in session.get("history", [])[-10:]:  # last 10 turns max
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    try:
        # Use prompt caching on system prompt
        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=300,
            system=[{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```json")[-1].split("```")[0].strip() if "```json" in text else text.split("```")[1].split("```")[0].strip()

        parsed = json.loads(text)

        # Python-level merge: if movie is missing but we have it from last search
        last = session.get("last_search")
        if last and parsed.get("action") == "search":
            if not parsed.get("movie") and last.get("movie"):
                parsed["movie"] = last["movie"]
            if not parsed.get("zipcode") and last.get("zipcode"):
                parsed["zipcode"] = last["zipcode"]

        return parsed
    except Exception as e:
        logger.error("Intent parsing failed: %s", e)
        return None


# --- Web Search (Sonnet 4.6 + web_search tool) ---

async def _web_search_movie(movie_raw: str, zipcode: str) -> dict:
    """Use Claude web search to find correct movie name, fun facts, and nearby theaters."""
    try:
        response = await client.messages.create(
            model=MODEL_SMART,
            max_tokens=800,
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=[{"role": "user", "content": f"""I need to find the movie "{movie_raw}" at Cinemark theaters near zipcode {zipcode}.

Search the web and tell me:
1. The correct full movie title (the user may have misspelled it)
2. The Cinemark URL slug for this movie (like "dhurandhar-the-revenge-hindi-with-english-subtitles")
3. Is it currently in theaters?

Return ONLY a JSON object:
{{"correct_name": "Full Movie Title", "cinemark_search": "url-slug-on-cinemark", "in_theaters": true}}

If you can't find the movie, return: {{"correct_name": "{movie_raw}", "cinemark_search": "{movie_raw.lower().replace(' ', '-')}", "in_theaters": false}}"""}],
        )

        # Extract text from response (may have tool use blocks mixed in)
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip()

        if not text:
            return {"correct_name": movie_raw, "cinemark_search": movie_raw.lower().replace(" ", "-")}

        if "```" in text:
            text = text.split("```json")[-1].split("```")[0].strip() if "```json" in text else text.split("```")[1].split("```")[0].strip()

        # Try to extract JSON from the text
        import re
        json_match = re.search(r'\{[^{}]+\}', text)
        if json_match:
            return json.loads(json_match.group())

        return json.loads(text)

    except Exception as e:
        logger.error("Web search failed: %s", e)
        return {"correct_name": movie_raw, "cinemark_search": movie_raw.lower().replace(" ", "-")}


# --- AI Ranking + Recommendation (Haiku 4.5) ---

async def _ai_rank_and_recommend(
    results: list, movie: str, num_seats: int, format_pref: str, zipcode: str, seat_pref: str = "best",
) -> tuple[list, str]:
    """AI ranks all results and gives recommendation."""

    if not results:
        return [], "No results to analyze."

    # Build summary for AI
    summary_lines = []
    for i, r in enumerate(results):
        best = r["seats"][0] if r["seats"] else None
        line = f"{i+1}. {r['theater']} | {r['date']} {r['time']} | {r['format']} | ${r.get('price',0):.2f} | {r['available']}/{r['total']} available"
        if best:
            line += f" | Best: {best['labels']} (score {best['score']:.2f}, {best['reasoning']})"
        summary_lines.append(line)

    summary = "\n".join(summary_lines)

    try:
        response = await client.messages.create(
            model=MODEL_FAST,
            max_tokens=400,
            messages=[{"role": "user", "content": f"""You're a movie seat advisor. Rank these options for "{movie}" ({num_seats} seats) near {zipcode}.
User prefers: format={format_pref}, seat preference={seat_pref}
(seat_pref: best=optimal viewing, back=back rows, front=front rows, aisle=edge seats, center=dead center)

{summary}

Return JSON with two fields:
1. "ranking": array of numbers (1-indexed) in order from best to worst. Consider: seat quality (score), theater format (IMAX>XD>Standard), price, availability (less crowded = better), showtime convenience.
2. "recommendation": 2-3 casual sentences. Be like a friend texting advice.

CRITICAL RULES for recommendation:
- NEVER say "option 1" or "option 3" or any number. ALWAYS use the theater name and time instead (e.g., "Cinemark Allen at 7:50 PM").
- ALWAYS mention the specific seat numbers (e.g., "seats D5, D6").
- If the best available seats are in Row A or B (front rows), explain most seats are sold out and these front rows are close to the screen.
- If a showtime has 0 available seats, say it's sold out.
- If availability is below 20%, warn it's filling up fast.

Example: {{"ranking": [3,1,5,2,4], "recommendation": "Grab D5-D6 at Cinemark Allen 7:50 PM — great center seats at 57% back with tons of availability. Skip Frisco Square 1 PM, it's completely sold out."}}"""}],
        )

        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```json")[-1].split("```")[0].strip() if "```json" in text else text.split("```")[1].split("```")[0].strip()

        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(text)

        ranking = data.get("ranking", list(range(1, len(results) + 1)))
        recommendation = data.get("recommendation", "Check the results above.")

        # Reorder results by AI ranking
        ranked = []
        for idx in ranking:
            if 1 <= idx <= len(results):
                ranked.append(results[idx - 1])
        # Add any results AI didn't rank
        for r in results:
            if r not in ranked:
                ranked.append(r)

        return ranked, recommendation

    except Exception as e:
        logger.error("AI ranking failed: %s", e)
        # Fallback: sort by score
        results.sort(key=lambda r: r["seats"][0]["score"] if r["seats"] else 0, reverse=True)
        best = results[0] if results else {}
        rec = f"Best option: {best.get('seats', [{}])[0].get('labels', 'N/A')} at {best.get('theater', 'N/A')}, {best.get('time', 'N/A')}."
        return results, rec


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
