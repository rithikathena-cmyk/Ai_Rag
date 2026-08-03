from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.term import TermModel


def get_or_create_term_ids(db: Session, terms: set[str]) -> dict[str, int]:
    if not terms:
        return {}
    stmt = pg_insert(TermModel).values([{"term": t} for t in terms]).on_conflict_do_nothing(
        index_elements=["term"]
    )
    db.execute(stmt)
    db.flush()
    rows = db.query(TermModel).filter(TermModel.term.in_(terms)).all()
    return {r.term: r.id for r in rows}
