"""Each module exposes `fetch() -> list[Opportunity]`."""
from . import devpost, devfolio, hackerearth, unstop, hackclub, kaggle, zindi, badgeup, mentorship, pathways, manual, aggregators

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
    # aggregator blogs (fellowships, scholarships, competitions, conferences) via WordPress JSON.
    # Order matters: the same program shows up on several of them and the first site to list it wins,
    # so the one with the cleanest deadlines/locations goes first.
    "opportunitiescorners": aggregators.fetch_opportunitiescorners,
    "oyaop": aggregators.fetch_oyaop,
    "opportunitydesk": aggregators.fetch_opportunitydesk,
    # curated
    "manual": manual.fetch,
}
