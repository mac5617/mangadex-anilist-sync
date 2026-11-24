"""Reasons and replies read plainly: the local model's hype words ("delivers", "unique") are taken out of what the
page shows, on For you, New releases and Ask alike."""

from types import SimpleNamespace

import pytest

from mdal.recommend import chat, fresh
from mdal.recommend.llm import dehype, tidy_reason, tidy_summary


@pytest.mark.parametrize("raw, expected", [
    # seen on real answers
    ("Delivers the same absurd, surreal comedy as One-Punch Man.", "Has the same absurd, surreal comedy as One-Punch Man."),
    ("Combines school horror with a unique plant-based threat.", "Combines school horror with a plant-based threat."),
    ("It shares the unique blend of dark humor found in Gantz.", "It shares the blend of dark humor found in Gantz."),
    ("Matches the fantasy of Old Knight with a unique food twist.", "Matches the fantasy of Old Knight with a food twist."),
    # more of the same family
    ("A gripping, compelling revenge story like Happiness.", "A revenge story like Happiness."),
    ("Unique art and a slow dread, like Uzumaki.", "Art and a slow dread, like Uzumaki."),
    ("It delivers a unique, eerie atmosphere close to Mieruko-chan.", "It has an eerie atmosphere close to Mieruko-chan."),
    ("A perfect fit for fans of Gantz, with high-octane fights.", "A good fit for fans of Gantz, with fights."),
    ("A horror masterpiece in the vein of Uzumaki.", "A horror classic in the vein of Uzumaki."),
    ("Both deliver brutal survival games, like Gantz.", "Both have brutal survival games, like Gantz."),
    # titles ending in ! or ? keep the next word as it was
    ("Like Gantz, BLAME! delivers a dark, oppressive setting.", "Like Gantz, BLAME! has a dark, oppressive setting."),
    ("Yotsuba to! offers a gentle slice of life, unlike Gantz.", "Yotsuba to! offers a gentle slice of life, unlike Gantz."),
    ("My New Girlfriend Is Not Human? provides a unique blend of romance.",
     "My New Girlfriend Is Not Human? provides a blend of romance."),
    # plain text is left alone
    ("Shares the creeping dread of Uzumaki.", "Shares the creeping dread of Uzumaki."),
    ("The courier delivers letters to the dead, like Mushishi.", "The courier delivers letters to the dead, like Mushishi."),
])
def test_dehype(raw, expected):
    assert dehype(raw) == expected


def test_every_cleaner_takes_hype_out():
    reason = "Delivers a unique blend of gore and comedy, like Gantz."
    plain = "Has a blend of gore and comedy, like Gantz."
    titles = [f"Cand {i}" for i in range(1, 13)]
    assert tidy_reason(reason, "Cand 1", titles) == plain
    assert tidy_summary("You love unique, gripping horror like Uzumaki.") == "You love horror like Uzumaki."
    assert fresh.clean_reason(reason, [SimpleNamespace(title=t) for t in titles]) == plain
    item = chat.Item(key="md:1", title="Cand 1", url="u", cover=None, meta=[], tags=[], description=None, taste=0.5,
                     adult=False, source="MangaDex")
    reply, picks = chat.clean_reply({"reply": "A unique pick for a weekend.", "picks": [{"n": 1, "reason": reason}]},
                                    [item])
    assert picks[0]["reason"] == plain and reply == "A pick for a weekend."
