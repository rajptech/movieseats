from movieseats.chains.base import ChainConfig

FANDANGO = ChainConfig(
    name="fandango",
    url="https://www.fandango.com",
    search_hints=(
        "Fandango has a search bar at the top of the page. Type the movie name "
        "directly into the search bar and press Enter. It will show results. "
        "If it asks for a location, enter the zipcode. You may also try "
        "navigating to fandango.com/movietimes and entering the zipcode there. "
        "Fandango aggregates showtimes from AMC, Regal, Cinemark, and others."
    ),
    showtime_hints=(
        "Showtimes are grouped by theater. Each showtime appears as a clickable "
        "button showing the time (e.g., '7:30 PM'). Clicking a showtime may "
        "redirect you to the theater chain's own website for seat selection."
    ),
    seat_map_hints=(
        "Fandango may redirect to the chain's website for seat selection. "
        "The seat map typically shows available seats as blue/colored circles "
        "and taken seats as gray/dark circles. The screen is usually shown "
        "at the top of the seat map."
    ),
    known_popups=[
        "[data-testid='cookie-banner'] button",
        ".ab-close-button",
        "#onetrust-accept-btn-handler",
    ],
)
