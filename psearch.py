"""Binary search-record format for IDA* runs on the 15-puzzle.

Layout:
    main file (.psearch):
        [HEADER_SIZE bytes header]
        [N nodes * NODE_SIZE bytes]   little-endian struct '<II'
    sidecar (.psearch.idx):
        compact binary index of solution path, iterations, depth buckets,
        sample IDs.

Node packing (struct '<II'):
    parent_id : uint32  (ROOT_PARENT for root)
    meta      : uint32  bit-packed:
        bits 0-1   : move idx (0=UP,1=DOWN,2=LEFT,3=RIGHT)
        bits 2-8   : depth   (7 bits, 0..127)
        bits 9-16  : f_value (8 bits, 0..255)
        bits 17-21 : iteration index (5 bits, 0..31)
        bits 22-31 : flags
            bit 0 : on_solution_path
            bit 1 : recorded_in_full_mode

The recorder uses a preallocated 1 MB bytearray and `struct.pack_into` for
O(1) appends, then flushes the buffer to disk when it fills up.
"""

from __future__ import annotations

import mmap
import os
import struct
import time
from typing import Optional

MAGIC = b'PSRCH\x01'
HEADER_SIZE = 512
NODE_SIZE = 8

ROOT_PARENT = 0xFFFFFFFF
_FLUSH_THRESHOLD = 1024 * 1024  # 1 MB

_MOVE_NAMES = ('UP', 'DOWN', 'LEFT', 'RIGHT')
_MOVE_IDX = {n: i for i, n in enumerate(_MOVE_NAMES)}

# Move deltas matched to astar.MOVE table (dx, dy) where index = move idx.
_MOVE_DELTA = (
    (1, 0),    # UP
    (-1, 0),   # DOWN
    (0, 1),    # LEFT
    (0, -1),   # RIGHT
)

# Per-node struct
_NODE_STRUCT = struct.Struct('<II')

# Sidecar magic
_IDX_MAGIC = b'PSIDX\x01'

# Main header: magic(6) version(B) mode(B) sample_every(I) force_depth_max(I)
#   total_recorded(Q) total_explored(Q) n_solution_moves(B) solution_moves(32 bytes)
#   n_iterations(I) n_depths(I) timestamp(d) start_state(16 bytes)
_HEADER_STRUCT = struct.Struct('<6sBBII Q Q B 32s I I d 16s')


def _pack_meta(move_idx: int, depth: int, f_value: int,
               iteration: int, flags: int) -> int:
    if depth > 127:
        depth = 127
    if f_value > 255:
        f_value = 255
    if iteration > 31:
        iteration = 31
    return (
        (move_idx & 0x3)
        | ((depth & 0x7F) << 2)
        | ((f_value & 0xFF) << 9)
        | ((iteration & 0x1F) << 17)
        | ((flags & 0x3FF) << 22)
    )


def _unpack_meta(meta: int):
    move_idx = meta & 0x3
    depth = (meta >> 2) & 0x7F
    f_value = (meta >> 9) & 0xFF
    iteration = (meta >> 17) & 0x1F
    flags = (meta >> 22) & 0x3FF
    return move_idx, depth, f_value, iteration, flags


class Recorder:
    """Streaming, append-only recorder. Hot path is O(1)."""

    def __init__(self, path: str, mode: str, start_state,
                 *, sample_every: int = 1000, force_depth_max: int = 8):
        if mode not in ('off', 'sampled', 'full'):
            raise ValueError("mode must be 'off', 'sampled', or 'full'")
        self.path = path
        self.mode = mode
        self.start_state = list(start_state)
        self.sample_every = max(1, int(sample_every))
        self.force_depth_max = int(force_depth_max)
        self.next_id = 0
        self._t0 = time.time()

        if mode == 'off':
            self._f = None
            self._buf = None
            self._buf_pos = 0
            return

        # Ensure parent directory exists
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._f = open(path, 'w+b')
        # reserve header
        self._f.write(b'\x00' * HEADER_SIZE)

        # preallocate 1 MB buffer
        self._buf = bytearray(_FLUSH_THRESHOLD)
        self._buf_pos = 0

        # Record root (parent=ROOT_PARENT, depth=0, move=0)
        # flags: full-mode bit set when mode=='full'
        flags = 0x2 if mode == 'full' else 0x0
        self._append_raw(ROOT_PARENT,
                         _pack_meta(0, 0, 0, 0, flags))

    # ------------------------------------------------------------------
    # hot path

    def _append_raw(self, parent_id: int, meta: int) -> int:
        nid = self.next_id
        if self._buf_pos + NODE_SIZE > len(self._buf):
            self._flush()
        _NODE_STRUCT.pack_into(self._buf, self._buf_pos, parent_id, meta)
        self._buf_pos += NODE_SIZE
        self.next_id = nid + 1
        return nid

    def _flush(self):
        if self._buf_pos == 0:
            return
        self._f.write(memoryview(self._buf)[:self._buf_pos])
        self._buf_pos = 0

    def record(self, parent_id, move_idx: int, depth: int,
               f_value: int, iteration: int) -> Optional[int]:
        if self.mode == 'off':
            return None

        if self.mode == 'full':
            flags = 0x2
            return self._append_raw(parent_id if parent_id is not None else ROOT_PARENT,
                                    _pack_meta(move_idx, depth, f_value,
                                               iteration, flags))

        # sampled mode
        if (parent_id is None
                or depth <= self.force_depth_max
                or (self.next_id % self.sample_every) == 0):
            flags = 0x0
            return self._append_raw(parent_id if parent_id is not None else ROOT_PARENT,
                                    _pack_meta(move_idx, depth, f_value,
                                               iteration, flags))
        return None

    # ------------------------------------------------------------------
    # solution path marking

    def mark_solution_path(self, node_ids):
        if self.mode == 'off' or not node_ids:
            return
        # ensure all buffered writes are on disk so we can seek+overwrite meta
        self._flush()
        self._f.flush()
        for nid in node_ids:
            if nid is None:
                continue
            # offset of meta field for node nid
            off = HEADER_SIZE + nid * NODE_SIZE + 4  # skip parent_id (4 bytes)
            # need to read existing meta to OR in flag
            self._f.seek(off)
            cur = self._f.read(4)
            if len(cur) != 4:
                continue
            meta = struct.unpack('<I', cur)[0] | (0x1 << 22)  # flag bit 0
            self._f.seek(off)
            self._f.write(struct.pack('<I', meta))
        self._f.flush()

    # ------------------------------------------------------------------
    # finalize

    def finalize(self, solution_moves, iterations,
                 nodes_per_depth, total_nodes: int):
        if self.mode == 'off':
            return

        self._flush()

        total_recorded = self.next_id

        # Build sidecar
        self._write_sidecar(solution_moves, iterations, nodes_per_depth)

        # Write header (overwrite reserved bytes)
        mode_byte = {'off': 0, 'sampled': 1, 'full': 2}[self.mode]
        n_solution_moves = len(solution_moves)
        if n_solution_moves > 32:
            n_solution_moves = 32
        moves_bytes = bytearray(32)
        for i in range(min(32, len(solution_moves))):
            mname = solution_moves[i]
            moves_bytes[i] = _MOVE_IDX.get(mname, 0)
        start_bytes = bytes(self.start_state[:16])
        if len(start_bytes) < 16:
            start_bytes = start_bytes + b'\x00' * (16 - len(start_bytes))

        header = _HEADER_STRUCT.pack(
            MAGIC,
            1,                          # version
            mode_byte,
            self.sample_every,
            self.force_depth_max,
            total_recorded,
            total_nodes,
            n_solution_moves,
            bytes(moves_bytes),
            len(iterations),
            len(nodes_per_depth),
            self._t0,
            start_bytes,
        )
        if len(header) > HEADER_SIZE:
            raise RuntimeError(f"header too big: {len(header)}")
        self._f.seek(0)
        self._f.write(header)
        # pad to HEADER_SIZE
        pad = HEADER_SIZE - len(header)
        if pad > 0:
            self._f.write(b'\x00' * pad)
        self._f.flush()
        self._f.close()
        self._f = None

    def _write_sidecar(self, solution_moves, iterations, nodes_per_depth):
        total_recorded = self.next_id
        # Choose sample IDs
        sample_target = 50_000
        if total_recorded <= sample_target:
            sample_ids = list(range(total_recorded))
        else:
            step = total_recorded / sample_target
            sample_ids = [int(i * step) for i in range(sample_target)]

        # depth buckets only if total_recorded < 200k
        build_buckets = total_recorded < 200_000
        depth_buckets = None
        if build_buckets and total_recorded > 0:
            # Need to scan recorded nodes from disk - but file already has data
            # written and we have not closed it yet. We can read via a second
            # handle. Simpler: re-open after closing main writer. But we
            # haven't written header yet. Use os.pread on the file descriptor
            # of self._f for the data region.
            depth_buckets = self._scan_depth_buckets()

        # Build sidecar binary
        idx_path = self.path + '.idx'
        with open(idx_path, 'wb') as idx:
            # Header: magic(6) version(B) n_nodes(Q) n_solution(I)
            #   n_iterations(I) n_depths(I) n_sample(I) n_buckets(I)
            idx_hdr = struct.pack(
                '<6sB Q I I I I I',
                _IDX_MAGIC,
                1,
                total_recorded,
                len(solution_moves),   # solution path id count == n moves + 1? we use IDs separately below
                len(iterations),
                len(nodes_per_depth),
                len(sample_ids),
                len(depth_buckets) if depth_buckets is not None else 0,
            )
            idx.write(idx_hdr)

            # solution_path_ids: we don't know IDs here directly; the caller of
            # finalize did mark_solution_path before finalize with the IDs.
            # Re-derive: scan recorded nodes flagged on_solution_path.
            sol_ids = self._collect_solution_ids()
            # Replace header n_solution with sol_ids count by rewriting that slot
            # (we wrote len(solution_moves) — fix it).
            idx.seek(0)
            idx_hdr2 = struct.pack(
                '<6sB Q I I I I I',
                _IDX_MAGIC, 1, total_recorded,
                len(sol_ids), len(iterations), len(nodes_per_depth),
                len(sample_ids), len(depth_buckets) if depth_buckets is not None else 0,
            )
            idx.write(idx_hdr2)
            # write arrays
            for sid in sol_ids:
                idx.write(struct.pack('<I', sid))
            for thr, cnt in iterations:
                idx.write(struct.pack('<BQ', min(255, int(thr)), int(cnt)))
            for c in nodes_per_depth:
                idx.write(struct.pack('<Q', int(c)))
            for sid in sample_ids:
                idx.write(struct.pack('<I', sid))
            if depth_buckets is not None:
                for d_ids in depth_buckets:
                    idx.write(struct.pack('<I', len(d_ids)))
                    for nid in d_ids:
                        idx.write(struct.pack('<I', nid))

    def _scan_depth_buckets(self):
        """Read all node records currently on disk and group by depth."""
        self._f.flush()
        # Use a separate read handle
        with open(self.path, 'rb') as r:
            r.seek(HEADER_SIZE)
            data = r.read(self.next_id * NODE_SIZE)
        buckets: list[list[int]] = []
        for nid in range(self.next_id):
            off = nid * NODE_SIZE
            _parent, meta = _NODE_STRUCT.unpack_from(data, off)
            _, depth, _, _, _ = _unpack_meta(meta)
            while len(buckets) <= depth:
                buckets.append([])
            buckets[depth].append(nid)
        return buckets

    def _collect_solution_ids(self):
        """Read meta of every node and return IDs flagged on_solution_path."""
        self._f.flush()
        with open(self.path, 'rb') as r:
            r.seek(HEADER_SIZE)
            data = r.read(self.next_id * NODE_SIZE)
        ids = []
        for nid in range(self.next_id):
            off = nid * NODE_SIZE
            _parent, meta = _NODE_STRUCT.unpack_from(data, off)
            _, _, _, _, flags = _unpack_meta(meta)
            if flags & 0x1:
                ids.append(nid)
        return ids


# ----------------------------------------------------------------------
# Reader

class Run:
    """Memory-mapped read view over a .psearch file + .idx sidecar."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, 'rb')
        size = os.fstat(self._f.fileno()).st_size
        if size < HEADER_SIZE:
            raise ValueError("file too small")
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)

        hdr = _HEADER_STRUCT.unpack_from(self._mm, 0)
        (magic, version, mode_byte, sample_every, force_depth_max,
         total_recorded, total_explored, n_solution_moves, moves_bytes,
         n_iterations, n_depths, timestamp, start_bytes) = hdr
        if magic != MAGIC:
            raise ValueError(f"bad magic: {magic!r}")
        self.version = version
        self.mode = {0: 'off', 1: 'sampled', 2: 'full'}.get(mode_byte, 'sampled')
        self.sample_every = sample_every
        self.force_depth_max = force_depth_max
        self.total_recorded = total_recorded
        self.total_explored = total_explored
        self.start_state = list(start_bytes)
        self.timestamp = timestamp
        self.solution_moves = [_MOVE_NAMES[moves_bytes[i] & 0x3]
                                for i in range(n_solution_moves)]

        # data region pointer
        self._data_off = HEADER_SIZE

        # caches
        self._children_index = None  # dict[int, list[int]]
        self._board_cache: dict[int, list[int]] = {}
        self._board_cache_order: list[int] = []

        # Load sidecar
        idx_path = path + '.idx'
        self.solution_path_ids: list[int] = []
        self.iterations: list[tuple[int, int]] = []
        self.nodes_per_depth: list[int] = []
        self.sample_ids: list[int] = []
        self._depth_buckets: Optional[list[list[int]]] = None

        if os.path.exists(idx_path):
            self._load_idx(idx_path)

        # derive simple stats
        self.max_depth = max(0, len(self.nodes_per_depth) - 1) if self.nodes_per_depth else 0
        # scan a sample for f bounds (cheap up to 10k)
        self.min_f = None
        self.max_f = None
        n_scan = min(self.total_recorded, 10_000)
        for nid in range(n_scan):
            _p, meta = _NODE_STRUCT.unpack_from(self._mm, self._data_off + nid * NODE_SIZE)
            _, _, f, _, _ = _unpack_meta(meta)
            if self.min_f is None or f < self.min_f:
                self.min_f = f
            if self.max_f is None or f > self.max_f:
                self.max_f = f
        if self.min_f is None:
            self.min_f = 0
        if self.max_f is None:
            self.max_f = 0

    def _load_idx(self, idx_path: str):
        with open(idx_path, 'rb') as f:
            raw = f.read()
        hdr_fmt = '<6sB Q I I I I I'
        hdr_size = struct.calcsize(hdr_fmt)
        (magic, version, n_nodes, n_solution, n_iters, n_depths,
         n_sample, n_buckets) = struct.unpack_from(hdr_fmt, raw, 0)
        if magic != _IDX_MAGIC:
            raise ValueError(f"bad idx magic: {magic!r}")
        pos = hdr_size
        # solution path ids
        for _ in range(n_solution):
            (sid,) = struct.unpack_from('<I', raw, pos); pos += 4
            self.solution_path_ids.append(sid)
        # iterations
        for _ in range(n_iters):
            thr, cnt = struct.unpack_from('<BQ', raw, pos); pos += 9
            self.iterations.append((thr, cnt))
        # nodes per depth
        for _ in range(n_depths):
            (c,) = struct.unpack_from('<Q', raw, pos); pos += 8
            self.nodes_per_depth.append(c)
        # sample ids
        for _ in range(n_sample):
            (sid,) = struct.unpack_from('<I', raw, pos); pos += 4
            self.sample_ids.append(sid)
        # depth buckets
        if n_buckets > 0:
            buckets = []
            for _ in range(n_buckets):
                (cnt,) = struct.unpack_from('<I', raw, pos); pos += 4
                d_ids = []
                for _2 in range(cnt):
                    (nid,) = struct.unpack_from('<I', raw, pos); pos += 4
                    d_ids.append(nid)
                buckets.append(d_ids)
            self._depth_buckets = buckets

    def __len__(self):
        return self.total_recorded

    def close(self):
        try:
            self._mm.close()
        finally:
            self._f.close()

    # ------------------------------------------------------------------
    # node access

    def _read_raw(self, nid: int):
        if nid < 0 or nid >= self.total_recorded:
            raise IndexError(nid)
        off = self._data_off + nid * NODE_SIZE
        return _NODE_STRUCT.unpack_from(self._mm, off)

    def node(self, nid: int) -> dict:
        parent_id, meta = self._read_raw(nid)
        move_idx, depth, f_value, iteration, flags = _unpack_meta(meta)
        if parent_id == ROOT_PARENT:
            mname = 'ROOT'
        else:
            mname = _MOVE_NAMES[move_idx]
        return {
            'parent_id': parent_id,
            'move': mname,
            'depth': depth,
            'f_value': f_value,
            'iteration': iteration,
            'on_path': bool(flags & 0x1),
        }

    def children(self, nid: int) -> list[int]:
        if self._children_index is None:
            self._build_children_index()
        return self._children_index.get(nid, [])

    def _build_children_index(self):
        idx: dict[int, list[int]] = {}
        for cid in range(self.total_recorded):
            parent_id, _ = self._read_raw(cid)
            if parent_id == ROOT_PARENT:
                continue
            idx.setdefault(parent_id, []).append(cid)
        self._children_index = idx

    def path_to_root(self, nid: int) -> list[str]:
        moves = []
        cur = nid
        while True:
            n = self.node(cur)
            if n['parent_id'] == ROOT_PARENT:
                break
            moves.append(n['move'])
            cur = n['parent_id']
        moves.reverse()
        return moves

    def reconstruct_board(self, nid: int) -> list[int]:
        if nid in self._board_cache:
            return list(self._board_cache[nid])
        moves = self.path_to_root(nid)
        board = list(self.start_state)
        try:
            zi = board.index(0)
        except ValueError:
            zi = 0
        for mname in moves:
            m_idx = _MOVE_IDX.get(mname)
            if m_idx is None:
                continue
            dx, dy = _MOVE_DELTA[m_idx]
            zx, zy = zi >> 2, zi & 3
            nx, ny = zx + dx, zy + dy
            if not (0 <= nx <= 3 and 0 <= ny <= 3):
                continue
            ni = (nx << 2) + ny
            board[zi] = board[ni]
            board[ni] = 0
            zi = ni
        # cache (LRU-ish, cap 64)
        self._board_cache[nid] = list(board)
        self._board_cache_order.append(nid)
        if len(self._board_cache_order) > 64:
            old = self._board_cache_order.pop(0)
            self._board_cache.pop(old, None)
        return board

    # ------------------------------------------------------------------
    # neighbors

    def neighbors(self, nid: int, axis: str):
        if axis == 'dfs':
            prev = nid - 1 if nid - 1 >= 0 else None
            nxt = nid + 1 if nid + 1 < self.total_recorded else None
            return prev, nxt

        if axis == 'solution':
            if nid in self.solution_path_ids:
                i = self.solution_path_ids.index(nid)
                prev = self.solution_path_ids[i - 1] if i > 0 else None
                nxt = self.solution_path_ids[i + 1] if i + 1 < len(self.solution_path_ids) else None
                return prev, nxt
            return None, None

        n = self.node(nid)
        if axis == 'depth':
            return self._neighbor_by_attr(nid, 'depth', n['depth'])
        if axis == 'f':
            return self._neighbor_by_attr(nid, 'f', n['f_value'])
        if axis == 'iteration':
            return self._neighbor_by_attr(nid, 'iteration', n['iteration'])
        raise ValueError(f"unknown axis: {axis}")

    def _neighbor_by_attr(self, nid: int, attr: str, value: int):
        # Use depth_buckets when applicable
        if attr == 'depth' and self._depth_buckets is not None:
            if value < len(self._depth_buckets):
                bucket = self._depth_buckets[value]
                if nid in bucket:
                    i = bucket.index(nid)
                    prev = bucket[i - 1] if i > 0 else None
                    nxt = bucket[i + 1] if i + 1 < len(bucket) else None
                    return prev, nxt
            return None, None

        # generic scan
        prev = None
        nxt = None
        for cid in range(nid - 1, -1, -1):
            _, meta = self._read_raw(cid)
            _, d, f, it, _ = _unpack_meta(meta)
            v = {'depth': d, 'f': f, 'iteration': it}[attr]
            if v == value:
                prev = cid
                break
        for cid in range(nid + 1, self.total_recorded):
            _, meta = self._read_raw(cid)
            _, d, f, it, _ = _unpack_meta(meta)
            v = {'depth': d, 'f': f, 'iteration': it}[attr]
            if v == value:
                nxt = cid
                break
        return prev, nxt

    def find_at_depth(self, d: int, k: int) -> int:
        if self._depth_buckets is not None and d < len(self._depth_buckets):
            bucket = self._depth_buckets[d]
            if 0 <= k < len(bucket):
                return bucket[k]
            raise IndexError(k)
        # scan
        seen = 0
        for cid in range(self.total_recorded):
            _, meta = self._read_raw(cid)
            _, depth, _, _, _ = _unpack_meta(meta)
            if depth == d:
                if seen == k:
                    return cid
                seen += 1
        raise IndexError(k)


# ----------------------------------------------------------------------
# Smoke test

def _smoke_test():
    import tempfile
    start = list(range(1, 16)) + [0]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'smoke.psearch')
        rec = Recorder(path, 'full', start, sample_every=10, force_depth_max=4)

        # Record 99 children to reach 100 total. Build a small tree.
        # Track recorder-assigned IDs.
        ids = [0]  # root is id 0
        for i in range(1, 100):
            parent = ids[(i - 1) // 3]  # branching factor ~3
            depth = (i % 20)
            f_val = (depth + 5) % 100
            move_idx = i % 4
            nid = rec.record(parent, move_idx, depth, f_val, i % 5)
            ids.append(nid)

        # mark some solution path (ids 0, 1, 2, 3)
        rec.mark_solution_path([0, 1, 2, 3])
        rec.finalize(
            solution_moves=['UP', 'DOWN', 'LEFT'],
            iterations=[(20, 50), (22, 30), (24, 20)],
            nodes_per_depth=[1, 3, 9, 27, 30, 20, 10],
            total_nodes=12345,
        )

        run = Run(path)
        assert len(run) == 100, f"expected 100, got {len(run)}"
        root = run.node(0)
        assert root['parent_id'] == ROOT_PARENT, f"root parent: {root['parent_id']}"
        ch = run.children(0)
        assert len(ch) > 0, "root has no children"
        board0 = run.reconstruct_board(0)
        assert board0 == start, f"root board mismatch: {board0}"
        prev, nxt = run.neighbors(50, 'dfs')
        assert prev == 49 and nxt == 51, f"neighbors mismatch: {prev}, {nxt}"
        # check solution path detected
        assert 0 in run.solution_path_ids
        assert 1 in run.solution_path_ids
        run.close()
    print("psearch smoke test passed")


if __name__ == '__main__':
    _smoke_test()
