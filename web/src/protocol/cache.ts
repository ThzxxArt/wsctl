export interface CachedScreen {
  session: string;
  generation: string;
  lastSeq: number;
  serialized: string;
  updatedAt: number;
}

const DB_NAME = "wsctl-workspace-v2";
const STORE = "screens";
//: Bumped when the meaning of ``lastSeq`` changed (snapshots no longer count
//: their rendered payload as stream bytes). Old entries must not be resumed.
const VERSION = 1;
//: A long-lived tab used to retain the full serialized screen of every session
//: it had ever opened. Bound the in-memory tier; IndexedDB is purged on logout
//: and on session removal.
const MAX_MEMORY = 64;
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const memory = new Map<string, CachedScreen>();
let database: Promise<IDBDatabase | null> | null = null;

function fresh(screen: CachedScreen | null): CachedScreen | null {
  if (!screen) return null;
  return Date.now() - screen.updatedAt <= MAX_AGE_MS ? screen : null;
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  // Cache the connection. This used to open a fresh IndexedDB connection on
  // every screen cache write (once per 1.2s per pane) and never close it,
  // leaking handles for the life of the tab.
  if (database !== null) return database;
  database = new Promise((resolve) => {
    const request = indexedDB.open(DB_NAME, VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "session" });
      }
    };
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => {
        db.close();
        database = null;
      };
      resolve(db);
    };
    request.onerror = () => {
      database = null;
      resolve(null);
    };
    request.onblocked = () => {
      database = null;
      resolve(null);
    };
  });
  return database;
}

export async function readScreen(session: string): Promise<CachedScreen | null> {
  const db = await openDatabase();
  if (!db) return fresh(memory.get(session) ?? null);
  return new Promise((resolve) => {
    let tx: IDBTransaction;
    try {
      tx = db.transaction(STORE, "readonly");
    } catch {
      resolve(memory.get(session) ?? null);
      return;
    }
    const request = tx.objectStore(STORE).get(session);
    request.onsuccess = () =>
      resolve(fresh((request.result as CachedScreen | undefined) ?? memory.get(session) ?? null));
    request.onerror = () => resolve(fresh(memory.get(session) ?? null));
  });
}

export async function writeScreen(screen: CachedScreen): Promise<void> {
  memory.delete(screen.session);
  memory.set(screen.session, screen);
  while (memory.size > MAX_MEMORY) {
    const oldest = memory.keys().next().value;
    if (oldest === undefined) break;
    memory.delete(oldest);
  }
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>((resolve) => {
    let tx: IDBTransaction;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch {
      resolve();
      return;
    }
    tx.objectStore(STORE).put(screen);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

export async function clearAllScreens(): Promise<void> {
  memory.clear();
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>((resolve) => {
    let tx: IDBTransaction;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch {
      resolve();
      return;
    }
    tx.objectStore(STORE).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

export async function clearScreen(session: string): Promise<void> {
  memory.delete(session);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>((resolve) => {
    let tx: IDBTransaction;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch {
      resolve();
      return;
    }
    tx.objectStore(STORE).delete(session);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}
