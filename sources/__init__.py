"""Each module exposes `fetch() -> list[Opportunity]`."""
from . import devpost, devfolio, hackerearth, unstop, hackclub, kaggle, zindi, badgeup, mentorship, pathways, manual

ALL = {
    # hackathons
    "devpost": devpost.fetch,
    "devfolio": devfolio.fetch,
    "hackerearth": hackerearth.fetch,
    "unstop": unstop.fetch,
    "hackclub": hackclub.fetch,
    # competitions
    "kaggle": kaggle.fetch,
    "zindi": zindi.fetch,
    # conferences
    "badgeup": badgeup.fetch,
    # research / fellowships
    "mentorship": mentorship.fetch,
    "pathways": pathways.fetch,        # opt-in: PATHWAYS_OK=1
    # curated
    "manual": manual.fetch,
}
