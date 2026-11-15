"""read (reading list), due, recall, quiz."""
from __future__ import annotations

from .. import reading
from ..library import Library


def add(sub):
    p = sub.add_parser("read", help="the reading list")
    s = p.add_subparsers(dest="read_cmd", metavar="<action>")
    q = s.add_parser("add", help="add a paper (or change its planned depth)")
    q.add_argument("key")
    q.add_argument("--depth", required=True, choices=reading.DEPTHS)
    q.add_argument("--note", default="")
    q.set_defaults(func=cmd_add)
    q = s.add_parser("done", help="record a finished paper; deep/read papers get a recall card")
    q.add_argument("key")
    q.add_argument("--depth", required=True, choices=reading.DEPTHS, help="the depth actually done")
    q.add_argument("--prompt", default="", help="recall prompt for the card")
    q.set_defaults(func=cmd_done)
    q = s.add_parser("list", help="show the reading list")
    q.add_argument("--all", action="store_true", help="include finished papers")
    q.set_defaults(func=cmd_list)
    p.set_defaults(func=cmd_list, all=False)

    p = sub.add_parser("due", help="recall cards due today and papers being read")
    p.set_defaults(func=cmd_due)

    p = sub.add_parser("recall", help="rate a recall card")
    p.add_argument("id", help="paper key or concept:<slug>")
    p.add_argument("rating", choices=reading.RATINGS)
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("quiz", help="quiz and discussion logs (quizzes/<key>.md)")
    s = p.add_subparsers(dest="quiz_cmd", metavar="<action>")
    q = s.add_parser("list", help="papers and concepts with logs (default)")
    q.set_defaults(func=cmd_quiz_list)
    q = s.add_parser("show", help="print a paper's log: read it before the next session")
    q.add_argument("id", help="paper key or concept:<slug>")
    q.set_defaults(func=cmd_quiz_show)
    q = s.add_parser("start", help="add a dated section for a new session")
    q.add_argument("id")
    q.add_argument("--discuss", action="store_true", help="a discussion, not a quiz")
    q.set_defaults(func=cmd_quiz_start)
    p.set_defaults(func=cmd_quiz_list)


def cmd_add(a):
    lib = Library.find(a.library)
    reading.add_to_list(lib, a.key, a.depth, note=a.note)
    print(f"{lib.get(a.key).key}: on the reading list ({a.depth})")


def cmd_done(a):
    lib = Library.find(a.library)
    reading.mark_done(lib, a.key, a.depth, prompt=a.prompt)
    card = " and a recall card" if a.depth in reading.RECALL_DEPTHS else ""
    print(f"{lib.get(a.key).key}: done ({a.depth}){card}")


def cmd_list(a):
    lib = Library.find(a.library)
    rows = [r for r in reading.load_list(lib) if a.all or r.get("status") != "done"]
    for r in rows:
        done = f"  done {r['done']['depth']} {r['done']['date']}" if r.get("done") else ""
        print(f"{r['key']:40s} {r['depth']:7s} {r.get('status', '')}{done}")
    if not rows:
        print("The reading list is empty.")


def cmd_due(a):
    lib = Library.find(a.library)
    d = reading.due(lib)
    if not d["recall"] and not d["reading"]:
        print("Nothing due.")
        return
    for c in d["recall"]:
        print(f"recall  {c['id']} (box {c.get('box')}): {c.get('prompt') or 'recall the gist'}")
    for r in d["reading"]:
        print(f"reading {r['key']} ({r['depth']})")


def cmd_recall(a):
    lib = Library.find(a.library)
    c = reading.rate(lib, a.id, a.rating)
    print(f"{a.id}: box {c['box']}, next due {c['next_due']}")


def cmd_quiz_list(a):
    from .. import quizzes
    lib = Library.find(a.library)
    rows = quizzes.listing(lib)
    for r in rows:
        print(f"{r['id']:40s} {r['sessions']:2d} session(s), last {r['last']}")
    if not rows:
        print("No quiz or discussion logs yet.")


def cmd_quiz_show(a):
    from .. import quizzes
    lib = Library.find(a.library)
    p = quizzes.path(lib, a.id)
    print(p.read_text("utf-8") if p.is_file() else f"No log yet for {a.id}.")


def cmd_quiz_start(a):
    from .. import quizzes
    lib = Library.find(a.library)
    print(quizzes.start(lib, a.id, "discussion" if a.discuss else "quiz"))
