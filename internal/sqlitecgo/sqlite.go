package sqlitecgo

/*
#cgo pkg-config: sqlite3
#include <sqlite3.h>
#include <stdlib.h>
*/
import "C"

import (
	"errors"
	"fmt"
	"sync"
	"unsafe"
)

type ValueKind int

const (
	Null ValueKind = iota
	Integer
	Text
	Blob
)

type Value struct {
	Kind ValueKind
	I64  int64
	Text string
	Blob []byte
}

type Row []Value

type DB struct {
	mu   sync.Mutex
	ptr  *C.sqlite3
	path string
}

type Tx struct{ db *DB }

func Open(path string) (*DB, error) {
	return openFlags(path, C.int(C.SQLITE_OPEN_READWRITE|C.SQLITE_OPEN_CREATE|C.SQLITE_OPEN_FULLMUTEX))
}

// OpenReadOnly never creates or migrates verification snapshots.
func OpenReadOnly(path string) (*DB, error) {
	return openFlags(path, C.int(C.SQLITE_OPEN_READONLY|C.SQLITE_OPEN_FULLMUTEX))
}

func openFlags(path string, flags C.int) (*DB, error) {
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	var ptr *C.sqlite3
	if rc := C.sqlite3_open_v2(cpath, &ptr, flags, nil); rc != C.SQLITE_OK {
		var msg string
		if ptr != nil {
			msg = C.GoString(C.sqlite3_errmsg(ptr))
			C.sqlite3_close(ptr)
		}
		return nil, fmt.Errorf("sqlite open %s: rc=%d %s", path, int(rc), msg)
	}
	db := &DB{ptr: ptr, path: path}
	C.sqlite3_busy_timeout(ptr, 5000)
	return db, nil
}

func (db *DB) Close() error {
	db.mu.Lock()
	defer db.mu.Unlock()
	if db.ptr == nil {
		return nil
	}
	rc := C.sqlite3_close(db.ptr)
	if rc != C.SQLITE_OK {
		return fmt.Errorf("sqlite close: %s", C.GoString(C.sqlite3_errmsg(db.ptr)))
	}
	db.ptr = nil
	return nil
}

func (db *DB) Path() string { return db.path }

func (db *DB) Exec(query string, args ...any) (int64, error) {
	db.mu.Lock()
	defer db.mu.Unlock()
	return exec(db.ptr, query, args...)
}

func (db *DB) Query(query string, args ...any) ([]Row, error) {
	db.mu.Lock()
	defer db.mu.Unlock()
	return queryRows(db.ptr, query, args...)
}

func (db *DB) WithTx(fn func(*Tx) error) error {
	db.mu.Lock()
	defer db.mu.Unlock()
	if _, err := exec(db.ptr, "BEGIN IMMEDIATE"); err != nil {
		return err
	}
	tx := &Tx{db: db}
	if err := fn(tx); err != nil {
		_, _ = exec(db.ptr, "ROLLBACK")
		return err
	}
	if _, err := exec(db.ptr, "COMMIT"); err != nil {
		_, _ = exec(db.ptr, "ROLLBACK")
		return err
	}
	return nil
}

func (tx *Tx) Exec(query string, args ...any) (int64, error) { return exec(tx.db.ptr, query, args...) }
func (tx *Tx) Query(query string, args ...any) ([]Row, error) {
	return queryRows(tx.db.ptr, query, args...)
}

func exec(db *C.sqlite3, sql string, args ...any) (int64, error) {
	stmt, err := prepare(db, sql)
	if err != nil {
		return 0, err
	}
	defer C.sqlite3_finalize(stmt)
	if err := bind(stmt, args); err != nil {
		return 0, err
	}
	rc := C.sqlite3_step(stmt)
	if rc != C.SQLITE_DONE && rc != C.SQLITE_ROW {
		return 0, sqliteErr(db, rc, sql)
	}
	return int64(C.sqlite3_changes64(db)), nil
}

func queryRows(db *C.sqlite3, sql string, args ...any) ([]Row, error) {
	stmt, err := prepare(db, sql)
	if err != nil {
		return nil, err
	}
	defer C.sqlite3_finalize(stmt)
	if err := bind(stmt, args); err != nil {
		return nil, err
	}
	cols := int(C.sqlite3_column_count(stmt))
	var out []Row
	for {
		rc := C.sqlite3_step(stmt)
		if rc == C.SQLITE_DONE {
			break
		}
		if rc != C.SQLITE_ROW {
			return nil, sqliteErr(db, rc, sql)
		}
		row := make(Row, cols)
		for i := 0; i < cols; i++ {
			typ := C.sqlite3_column_type(stmt, C.int(i))
			switch typ {
			case C.SQLITE_INTEGER:
				row[i] = Value{Kind: Integer, I64: int64(C.sqlite3_column_int64(stmt, C.int(i)))}
			case C.SQLITE_TEXT:
				p := C.sqlite3_column_text(stmt, C.int(i))
				n := C.sqlite3_column_bytes(stmt, C.int(i))
				row[i] = Value{Kind: Text, Text: C.GoStringN((*C.char)(unsafe.Pointer(p)), n)}
			case C.SQLITE_BLOB:
				p := C.sqlite3_column_blob(stmt, C.int(i))
				n := C.sqlite3_column_bytes(stmt, C.int(i))
				if p != nil && n > 0 {
					row[i] = Value{Kind: Blob, Blob: C.GoBytes(p, n)}
				} else {
					row[i] = Value{Kind: Blob, Blob: []byte{}}
				}
			default:
				row[i] = Value{Kind: Null}
			}
		}
		out = append(out, row)
	}
	return out, nil
}

func prepare(db *C.sqlite3, sql string) (*C.sqlite3_stmt, error) {
	csql := C.CString(sql)
	defer C.free(unsafe.Pointer(csql))
	var stmt *C.sqlite3_stmt
	rc := C.sqlite3_prepare_v2(db, csql, -1, &stmt, nil)
	if rc != C.SQLITE_OK {
		return nil, sqliteErr(db, rc, sql)
	}
	return stmt, nil
}

func bind(stmt *C.sqlite3_stmt, args []any) error {
	expected := int(C.sqlite3_bind_parameter_count(stmt))
	if expected != len(args) {
		return fmt.Errorf("sqlite bind count: query expects %d args, got %d", expected, len(args))
	}
	for i, arg := range args {
		idx := C.int(i + 1)
		var rc C.int
		switch v := arg.(type) {
		case nil:
			rc = C.sqlite3_bind_null(stmt, idx)
		case string:
			c := C.CString(v)
			rc = C.sqlite3_bind_text(stmt, idx, c, C.int(len(v)), C.SQLITE_TRANSIENT)
			C.free(unsafe.Pointer(c))
		case []byte:
			if len(v) == 0 {
				rc = C.sqlite3_bind_blob(stmt, idx, nil, 0, C.SQLITE_TRANSIENT)
			} else {
				rc = C.sqlite3_bind_blob(stmt, idx, unsafe.Pointer(&v[0]), C.int(len(v)), C.SQLITE_TRANSIENT)
			}
		case int:
			rc = C.sqlite3_bind_int64(stmt, idx, C.sqlite3_int64(v))
		case int64:
			rc = C.sqlite3_bind_int64(stmt, idx, C.sqlite3_int64(v))
		case uint64:
			if v > uint64(^uint64(0)>>1) {
				return errors.New("uint64 value exceeds sqlite signed INTEGER")
			}
			rc = C.sqlite3_bind_int64(stmt, idx, C.sqlite3_int64(v))
		case bool:
			if v {
				rc = C.sqlite3_bind_int64(stmt, idx, 1)
			} else {
				rc = C.sqlite3_bind_int64(stmt, idx, 0)
			}
		default:
			return fmt.Errorf("unsupported sqlite bind type %T", arg)
		}
		if rc != C.SQLITE_OK {
			return fmt.Errorf("sqlite bind arg %d failed rc=%d", i+1, int(rc))
		}
	}
	return nil
}

func sqliteErr(db *C.sqlite3, rc C.int, sql string) error {
	return fmt.Errorf("sqlite rc=%d: %s; sql=%q", int(rc), C.GoString(C.sqlite3_errmsg(db)), sql)
}

func Version() string { return C.GoString(C.sqlite3_libversion()) }

func TextAt(row Row, i int) (string, error) {
	if i >= len(row) || row[i].Kind == Null {
		return "", nil
	}
	if row[i].Kind != Text {
		return "", fmt.Errorf("column %d is not text", i)
	}
	return row[i].Text, nil
}
func Int64At(row Row, i int) (int64, error) {
	if i >= len(row) || row[i].Kind != Integer {
		return 0, fmt.Errorf("column %d is not integer", i)
	}
	return row[i].I64, nil
}
