from movieseats.chains.base import ChainConfig

CINEMARK = ChainConfig(
    name="cinemark",
    url="https://www.cinemark.com",
    search_hints=(
        "Cinemark has a 'Movies' tab in the top navigation. Click it to see "
        "currently showing movies. There is also a location/zipcode search bar "
        "at the top — enter the zipcode to find nearby Cinemark theaters. "
        "You can search for a specific movie by name using the search icon. "
        "The URL pattern cinemark.com/movies shows all current movies."
    ),
    showtime_hints=(
        "Showtimes are shown per theater location. Each showtime is a clickable "
        "button or link with the time (e.g., '8:20pm', '9:35pm'). Use click_text "
        "with the exact time text to click a showtime. Look for format indicators: "
        "'XD' (premium), 'RealD 3D', 'D-BOX', 'Standard'. Clicking a showtime "
        "takes you to the TicketSeatMap page for seat selection. If clicking the "
        "time text doesn't work, try clicking the link/button that wraps it."
    ),
    seat_map_hints=(
        "Cinemark seat map: available seats are shown as outlined/open circles, "
        "taken seats are filled/solid/dark. The screen indicator is at the top. "
        "Row letters are on the sides. Some seats may be marked as wheelchair "
        "or companion seats with special symbols. The seat map URL pattern is "
        "cinemark.com/TicketSeatMap/."
    ),
    known_popups=[
        "#onetrust-accept-btn-handler",
        "button[class*='cookie']",
        "[aria-label='Close']",
    ],
)
