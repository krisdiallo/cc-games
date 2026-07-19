"""Game registry: maps config names to Game classes.

A Game implements:
    name       str, config/stats key
    title      str, header text
    keys_help  str, game part of the footer
    play_round(shell) -> True (correct) | False (wrong) | None (skipped)
"""

from . import dungeon, nback, sequences, simon, snake, trivia, words

REGISTRY = {
    "trivia": trivia.TriviaGame,
    "sequences": sequences.SequencesGame,
    "words": words.WordsGame,
    "simon": simon.SimonGame,
    "nback": nback.NBackGame,
    "dungeon": dungeon.DungeonGame,
    "snake": snake.SnakeGame,
}


def build_games(cfg, questions_path):
    """Instantiate the games enabled in config, skipping unknown names and any
    game whose content fails to load (e.g. an empty trivia bank)."""
    out = []
    for name in cfg.get("games", list(REGISTRY)):
        cls = REGISTRY.get(name)
        if cls is None:
            continue
        try:
            game = (cls(cfg, questions_path) if name == "trivia" else cls(cfg))
        except Exception:
            continue
        if game.playable():
            out.append(game)
    return out
