# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance-wide rolling spend admission, serialized durably before transport.

SQLite contains one state row per attempt, not an ever-growing event replay.
Expired settlements remain compact idempotency tombstones; unresolved bounds
never expire. Job JSONL remains the user-facing evidence, not the shared lock.
"""
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .ledger import Ledger

WINDOW = 86400

class BudgetError(Exception):
    def __init__(self, code='instance_budget_unavailable'):
        self.code = code
        self.before_dispatch = False
        Exception.__init__(self, 'Shared AI review budget is exhausted; source-only conversion remains available.'
            if code == 'instance_budget_exhausted' else
            'Shared AI review budget is unavailable. An administrator must configure or repair it; source-only conversion remains available.')

def money(value):
    try:
        if isinstance(value, bool):raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:raise ValueError()
        return result
    except (ValueError, InvalidOperation):raise BudgetError() from None

class Store:
    def __init__(self, root, budget, clock=time.time):
        self.root = Path(root)
        self.path = self.root / 'financial-admission.sqlite3'
        self.budget = budget
        self.clock = clock

    def _cap(self):
        cap = money(self.budget())
        if cap <= 0:raise BudgetError()
        return cap

    @contextmanager
    def _locked(self):
        db = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('pending','settled','released')), bound TEXT NOT NULL, cost TEXT, created REAL NOT NULL CHECK(created>=0 AND created<1e20), settled REAL, identity TEXT NOT NULL, CHECK((state='pending' AND cost IS NULL AND settled IS NULL) OR (state!='pending' AND cost IS NOT NULL AND settled>=0 AND settled<1e20)))""")
            db.execute('CREATE INDEX IF NOT EXISTS liability_window ON attempts(state,settled)')
            db.execute('CREATE TABLE IF NOT EXISTS imported (path TEXT PRIMARY KEY, digest TEXT NOT NULL, size INTEGER NOT NULL)')
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise BudgetError()
            blocked = db.execute("SELECT value FROM meta WHERE key='blocked'").fetchone()
            if blocked:raise BudgetError()
            now = float(self.clock())
            last = db.execute("SELECT value FROM meta WHERE key='clock'").fetchone()
            if not math.isfinite(now) or now < 0 or (last and (not math.isfinite(float(last[0])) or now < float(last[0]))):raise BudgetError()
            db.execute("INSERT OR REPLACE INTO meta VALUES ('clock',?)", (str(now),))
            self._import_jobs(db, now)
            # Settled rows remain compact idempotency tombstones; the indexed
            # accounting query touches only pending and the active24h window.
            yield db, now
            db.commit()
            fd = os.open(str(self.root), os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        except BudgetError:
            if db:db.commit()  # preserve a conflict marker or imported liability
            raise
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, OverflowError):
            if db:db.rollback()
            raise BudgetError() from None
        finally:
            if db:db.close()

    @staticmethod
    def _conflict(db):
        db.execute("INSERT OR REPLACE INTO meta VALUES ('blocked','conflicting financial evidence')")
        raise BudgetError()

    def _import_jobs(self, db, now):
        """Reconcile existing installations and crash-stale mirrors under lock.

        Read strict JSON, including the last line. Financial corruption is never
        treated as zero. Hashes make unchanged legacy files cheap to skip; changed
        files are replayed with stable identities, never charged twice.
        """
        for path in sorted((self.root/'jobs').glob('*/*.jsonl')):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest();relative=str(path.relative_to(self.root))
            prior = db.execute('SELECT digest,size FROM imported WHERE path=?', (relative,)).fetchone()
            if prior and prior[0] == digest:continue
            if prior and hashlib.sha256(raw[:prior[1]]).hexdigest()!=prior[0]:self._conflict(db)
            entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if any(not isinstance(e,dict) for e in entries):raise BudgetError()
            lifecycle=[e for e in entries if e.get('kind')=='job' and e.get('event') in ('start','finish')]
            current=lifecycle[-1] if lifecycle else {}
            if current.get('event')=='start' and money(current.get('cap_usd',0))>0 and current.get('shared_budget_version')!=1:
                # An old worker without shared admission may still open a socket.
                # Wait for its terminal/recovery record before enabling new work.
                raise BudgetError()
            states = {};pages = []
            for index,e in enumerate(entries):
                if e.get('kind') != 'reservation' and e.get('cost_usd') is None:continue
                ts=float(e['ts'])
                if not math.isfinite(ts) or ts<0 or ts>now+.00051:raise BudgetError()
                attempt=e.get('attempt')
                if e.get('kind')=='reservation':
                    if not isinstance(attempt,str) or not attempt:raise BudgetError()
                    state=states.setdefault(attempt,{})
                    event=e.get('event')
                    if event=='pending':
                        bound=money(e['bound_usd'])
                        if bound<=0 or (state and (state.get('state')!='pending' or state.get('bound')!=bound or state.get('created')!=ts)):self._conflict(db)
                        state.update(state='pending',bound=bound,created=ts)
                    elif event in ('reconciled','released'):
                        terminal='settled' if event=='reconciled' else 'released'
                        cost=money(e['cost_usd']) if terminal=='settled' else Decimal(0)
                        if not state:raise BudgetError()
                        if ts<state.get('settled',state['created']):self._conflict(db)
                        if state['state']!='pending' and (state['state']!=terminal or state['cost']!=cost):self._conflict(db)
                        state.update(state=terminal,cost=cost,settled=ts)
                    else:raise BudgetError()
                else:pages.append((index,e,ts))
            for index,e,ts in pages:
                attempt=e.get('attempt');cost=money(e['cost_usd'])
                if attempt in states and states[attempt]['state']=='settled':continue
                # Legacy page-only billing has no strict reservation identity.
                if attempt in states and states[attempt]['state']=='pending':
                    states[attempt].update(state='settled',cost=cost,settled=ts)
                else:
                    key='legacy:'+hashlib.sha256((relative+':'+str(attempt or index)).encode()).hexdigest()
                    previous=states.get(key)
                    if previous and previous['cost']!=cost:self._conflict(db)
                    states[key]=dict(state='settled',bound=cost,cost=cost,created=ts,settled=ts)
            for attempt,s in states.items():
                existing=db.execute('SELECT state,bound,cost,identity FROM attempts WHERE id=?',(attempt,)).fetchone()
                if existing:
                    state,bound,cost,identity=existing
                    if money(bound)!=s['bound']:self._conflict(db)
                    origin=json.loads(identity).get('ledger')
                    if origin and origin!=relative:self._conflict(db)
                    if state!='pending':
                        if s['state']!='pending' and (state!=s['state'] or money(cost)!=s['cost']):self._conflict(db)
                        continue  # global settlement survives a crash-stale pending mirror
                    if s['state']!='pending':
                        db.execute('UPDATE attempts SET state=?,cost=?,settled=? WHERE id=?',(s['state'],str(s['cost']),s['settled'],attempt))
                else:
                    db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',(attempt,s['state'],str(s['bound']),str(s['cost']) if 'cost' in s else None,s['created'],s.get('settled'),json.dumps({'ledger':relative})))
            db.execute('INSERT OR REPLACE INTO imported VALUES (?,?,?)',(relative,digest,len(raw)))

    @staticmethod
    def _liability(db, now):
        rows=db.execute("SELECT state,bound,cost FROM attempts WHERE state='pending' OR (state='settled' AND settled>?)",(now-WINDOW,))
        return sum((money(bound if state=='pending' else cost) for state,bound,cost in rows),Decimal(0))

    def status(self):
        try:cap=self._cap()
        except BudgetError:return dict(status='disabled',remaining_usd=None,window_hours=24)
        try:
            with self._locked() as (db,now):
                remaining=max(Decimal(0),cap-self._liability(db,now))
                return dict(status='available' if remaining else 'exhausted',remaining_usd=float(remaining),window_hours=24)
        except BudgetError:return dict(status='unavailable',remaining_usd=None,window_hours=24)

    def reserve(self, attempt, bound, *, job, book, user, context, ledger=None):
        bound=money(bound)
        if not isinstance(attempt,str) or not attempt or bound<=0:raise BudgetError()
        with self._locked() as (db,now):
            cap=self._cap()  # live setting read while shared admission is serialized
            if db.execute('SELECT 1 FROM attempts WHERE id=?',(attempt,)).fetchone():raise BudgetError()
            if self._liability(db,now)+bound>cap:raise BudgetError('instance_budget_exhausted')
            identity=dict(job=str(job),book=str(book),user=str(user),context=context)
            if ledger:identity['ledger']=str(Path(ledger).relative_to(self.root))
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',(attempt,'pending',str(bound),None,now,None,json.dumps(identity,sort_keys=True)))

    def finish(self, attempt, cost=None):
        state='released' if cost is None else 'settled';cost=Decimal(0) if cost is None else money(cost)
        with self._locked() as (db,now):
            row=db.execute('SELECT state,cost FROM attempts WHERE id=?',(attempt,)).fetchone()
            if not row:raise BudgetError()
            if row[0]!='pending':
                if row[0]!=state or money(row[1])!=cost:self._conflict(db)
                return
            db.execute('UPDATE attempts SET state=?,cost=?,settled=? WHERE id=?',(state,str(cost),now,attempt))

class SharedLedger(Ledger):
    def __init__(self,*args,store,book_id,user_id,**kwargs):
        super().__init__(*args,**kwargs)
        self.store=store;self.book_id=book_id;self.user_id=user_id

    def record(self,entry,durable=False):
        entry=dict(entry);entry.setdefault('ts',self.store.clock())
        if entry.get('kind')=='job' and entry.get('event')=='start':entry['shared_budget_version']=1
        return super().record(entry,durable=durable)

    def reserve_attempt(self,page_label,bound_usd,model_id='',prompt_version='',context=None):
        self.reserve(bound_usd)
        attempt=uuid.uuid4().hex
        safe={k:v for k,v in (context or {}).items() if k in ('stage','request_sha256','snapshot_id','proposal_id','route_version')}
        try:
            self.store.reserve(attempt,bound_usd,job=self.job_id,book=self.book_id,user=self.user_id,context=safe,ledger=self.path)
        except BudgetError as exc:
            exc.before_dispatch=True
            raise
        try:
            self.record(dict(kind='reservation',event='pending',attempt=attempt,page_label=str(page_label or ''),bound_usd=float(bound_usd),model=model_id,prompt_version=prompt_version,**safe),durable=True)
        except Exception:
            # No socket opened. If this release itself fails, keep the global hold.
            self.store.finish(attempt)
            raise
        return attempt

    def release_attempt(self,attempt,reason=''):
        if reason not in ('pre_dispatch','rejected_before_generation'):raise BudgetError()
        self.store.finish(attempt)
        self.record(dict(kind='reservation',event='released',attempt=attempt,reason=reason),durable=True)

    def reconcile_attempt(self,attempt,cost_usd):
        self.store.finish(attempt,cost_usd)
        self.record(dict(kind='reservation',event='reconciled',attempt=attempt,cost_usd=float(cost_usd)),durable=True)
