"""Durable one-time personal clocks, owned by a contest and an exam number."""
import time

from . import contest_time as ct, database, store


def all_for(cid):
    with database.connection(store.DATA_DIR) as db:
        return {kh: {"start_at": start, "end_at": end} for kh, start, end in
                db.execute("SELECT kaohao,start_at,end_at FROM exam_attempts WHERE contest_id=?", (cid,))}


def get(cid, kaohao):
    if not kaohao:
        return None
    with database.connection(store.DATA_DIR) as db:
        row = db.execute("SELECT start_at,end_at FROM exam_attempts WHERE contest_id=? AND kaohao=?", (cid, kaohao)).fetchone()
        return {"start_at": row[0], "end_at": row[1]} if row else None


def start(cid, kaohao):
    # Config, roster and clock are read under one lock/transaction. Two tabs can
    # never grant two attempts, and a reconnect never overwrites the first clock.
    with store._LOCK, database.transaction(store.DATA_DIR):
        contest = store.get_contest(cid)
        if not contest or kaohao not in store.load_roster(cid):
            raise ValueError("考号已失效，请重新登录。")
        if not ct.flexible(contest):
            raise ValueError("本场采用统一开考，无需个人开考。")
        existing = get(cid, kaohao)
        if existing:
            return existing
        now = time.time()
        opening, closing = ct.window(contest)
        if not opening or not opening <= now < closing:
            raise ct.ClosedContest("当前不在允许开考时间段内，无法开始考试。")
        end = now + store.duration_of(contest) * 60
        if not ct.timestamp(end):
            raise ValueError("考试截止时间超出有效范围。")
        with database.connection(store.DATA_DIR) as db:
            db.execute("INSERT INTO exam_attempts(contest_id,kaohao,start_at,end_at) VALUES(?,?,?,?)", (cid, kaohao, now, end))
        return {"start_at": now, "end_at": end}


def delete_contest(cid):
    with database.connection(store.DATA_DIR) as db:
        db.execute("DELETE FROM exam_attempts WHERE contest_id=?", (cid,))
