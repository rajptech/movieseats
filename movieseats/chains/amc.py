from movieseats.chains.base import ChainConfig

AMC = ChainConfig(
    name="amc",
    url="https://www.amctheatres.com",
    search_hints=(
        "AMC has a search icon (magnifying glass) in the top navigation bar. "
        "Click it and type the movie name. You can also click 'Movies' in the "
        "navigation to browse current movies. AMC may ask for your location — "
        "enter the zipcode when prompted. You can also try the URL pattern: "
        "amctheatres.com/movies to see all movies, then click one."
    ),
    showtime_hints=(
        "Showtimes appear under the movie, grouped by theater location. "
        "Each showtime is a clickable button with the time. Look for format "
        "labels like 'IMAX', 'Dolby Cinema', 'Standard'. Click a time to "
        "proceed to seat selection."
    ),
    seat_map_hints=(
        "AMC seat map: available seats are shown as teal/green circles, "
        "taken/occupied seats are dark/gray. Selected seats turn red/highlighted. "
        "Row letters are labeled on the left side. The screen is shown at the "
        "top of the map. Wheelchair and companion seats have special icons."
    ),
    known_popups=[
        "#onetrust-accept-btn-handler",
        "[data-testid='close-button']",
        ".modal-close",
        "[aria-label='Close']",
    ],
)
