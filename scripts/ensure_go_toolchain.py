#!/usr/bin/env python3
"""Pinned Go bootstrap: verified cache reuse, bounded download, staged installation."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time

VERSION = '1.27.1'
SHA256 = {
    'amd64': '63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445',
    'arm64': '3450b45a3f9ee8568792736a5c5e70a1f2e9b36c35a8f74958c03e51d7d92bec',
}
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def log(message):
    print('[go-bootstrap] ' + message, flush=True)


@contextlib.contextmanager
def install_lock(path, timeout=60):
    with path.open('a') as lock:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end: raise RuntimeError('another Go bootstrap is still running')
                time.sleep(.1)
        try: yield
        finally: fcntl.flock(lock, fcntl.LOCK_UN)


class Installer:
    def __init__(self, root, cache=None, *, version=VERSION, arch=None, expected=None,
                 runner=subprocess.run, pause=time.sleep):
        self.root = Path(root).resolve()
        self.version = version
        self.arch = arch or {'x86_64':'amd64', 'aarch64':'arm64', 'arm64':'arm64'}.get(platform.machine())
        if self.arch not in SHA256: raise ValueError('unsupported Go architecture: '+platform.machine())
        self.expected = expected or SHA256[self.arch]
        self.asset = f'go{version}.linux-{self.arch}.tar.gz'
        cache_base = os.environ.get('XDG_CACHE_HOME') or str(Path.home()/'.cache')
        self.cache = Path(cache or os.environ.get('EVICON_TOOLCHAIN_CACHE') or Path(cache_base)/'evicon-toolchains').expanduser().resolve()
        self.target = self.root/'.tools/go'
        self.backup = self.root/'.tools/.go-previous'
        self.runner, self.pause = runner, pause
        self.events = []

    def verified(self, path):
        try: return Path(path).is_file() and digest(path) == self.expected
        except OSError: return False

    def healthy(self, directory):
        directory = Path(directory)
        essential = ['bin/go', 'pkg/include/textflag.h', 'src/runtime/runtime2.go']
        essential += [f'pkg/tool/linux_{self.arch}/{tool}' for tool in ('compile','link','asm')]
        if any(not (directory/name).is_file() for name in essential): return False
        if any(not os.access(directory/name, os.X_OK) for name in [essential[0], *essential[3:]]): return False
        try:
            p = self.runner([str(directory/'bin/go'), 'version'], capture_output=True, text=True,
                            timeout=10, env=dict(os.environ, GOTOOLCHAIN='local', GOROOT=str(directory.resolve())))
            return p.returncode == 0 and p.stdout.strip() == f'go version go{self.version} linux/{self.arch}'
        except (OSError, subprocess.SubprocessError): return False

    def publish_cache(self, source):
        self.cache.mkdir(parents=True, exist_ok=True)
        destination = self.cache/self.asset
        if self.verified(destination): return destination
        fd, name = tempfile.mkstemp(prefix=self.asset+'.copy-', suffix='.part', dir=self.cache)
        os.close(fd); tmp = Path(name)
        try:
            shutil.copyfile(source, tmp)
            if not self.verified(tmp): raise RuntimeError('archive changed while copying to Go cache')
            with tmp.open('rb') as stream: os.fsync(stream.fileno())
            os.replace(tmp, destination)
        finally: tmp.unlink(missing_ok=True)
        return destination

    def candidates(self):
        yield self.cache/self.asset
        yield self.root/'.isolated'/self.asset
        # Read archive bytes only; never execute binaries from another release.
        # earlier EviCon releases next to this one cached the same pinned archive
        for previous in sorted(self.root.parent.glob('EVICON_TOIT_*'), reverse=True):
            if previous.is_dir() and previous.resolve() != self.root:
                yield previous/'.isolated'/self.asset

    def obtain_archive(self, explicit=None, offline=False):
        if explicit is not None:
            explicit = Path(explicit).expanduser().resolve()
            if not self.verified(explicit): raise ValueError('explicit Go archive is missing or fails pinned SHA-256: '+str(explicit))
            self.events.append({'source':'EXPLICIT_ARCHIVE','path':str(explicit)})
            return self.publish_cache(explicit)
        for candidate in self.candidates():
            if self.verified(candidate):
                log('reusing SHA-256 verified archive: '+str(candidate))
                self.events.append({'source':'VERIFIED_CACHE','path':str(candidate)})
                return self.publish_cache(candidate)
            if candidate.exists():
                log('ignoring archive with wrong SHA-256: '+str(candidate))
                self.events.append({'source':'REJECTED_CACHE','path':str(candidate)})
        if offline: raise RuntimeError('offline Go bootstrap: no archive matching pinned SHA-256 was found')
        self.cache.mkdir(parents=True, exist_ok=True)
        urls = ['https://go.dev/dl/'+self.asset, 'https://dl.google.com/go/'+self.asset]
        for attempt in range(1,5):
            fd, name = tempfile.mkstemp(prefix=self.asset+'.download-', suffix='.part', dir=self.cache)
            os.close(fd); tmp = Path(name)
            url = urls[(attempt-1)%len(urls)]
            log(f'download attempt {attempt}/4 (connect 15s, transfer 120s): {url}')
            event = {'source':'DOWNLOAD', 'attempt':attempt, 'url':url}
            try:
                # The explicit loop retries every curl error, including TLS EOF
                # (35). TLS and the frozen archive checksum are never disabled.
                p = self.runner(['curl','--fail','--location','--show-error','--silent',
                                 '--proto','=https','--proto-redir','=https',
                                 '--connect-timeout','15','--max-time','120',
                                 '--output',str(tmp),url], capture_output=True, text=True, timeout=125)
                event.update(returncode=p.returncode, detail=p.stderr[-2000:])
                if p.returncode == 0 and self.verified(tmp):
                    event['status']='VERIFIED';self.events.append(event)
                    with tmp.open('rb') as stream: os.fsync(stream.fileno())
                    destination = self.cache/self.asset
                    os.replace(tmp, destination)
                    return destination
                event['status']='REJECTED'
                if p.returncode == 0: event['detail']='download completed but pinned SHA-256 mismatch'
            except (OSError, subprocess.SubprocessError) as exc:
                event.update(status='FAILED', detail=str(exc)[-2000:])
            finally: tmp.unlink(missing_ok=True)
            self.events.append(event)
            log(f"attempt {attempt}/4 failed: {event['detail']}")
            if attempt < 4: self.pause(2*attempt)
        raise RuntimeError('Go download failed after 4 bounded attempts; previous toolchain kept. '
                           'Keep the previous release beside this source to reuse its verified archive, '
                           'or use scripts/ensure_go_toolchain.py --archive /path/to/'+self.asset+' --offline')

    def extract(self, archive, staging):
        # Hash again immediately before opening the archive for extraction.
        if not self.verified(archive): raise RuntimeError('Go archive checksum changed before extraction')
        with tarfile.open(archive, 'r:gz') as tf:
            for member in tf.getmembers():
                rel=Path(member.name)
                if (rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0]!='go'
                        or not (member.isfile() or member.isdir())):
                    raise ValueError('unsafe Go archive member: '+member.name)
            tf.extractall(staging, **({'filter':'data'} if hasattr(tarfile, 'data_filter') else {}))

    @staticmethod
    def remove_tree(path):
        if path.is_symlink(): path.unlink()
        elif path.exists(): shutil.rmtree(path)

    def recover(self):
        if not (self.backup.exists() or self.backup.is_symlink()): return
        if self.healthy(self.target):
            self.remove_tree(self.backup)
        else:
            self.remove_tree(self.target)
            os.replace(self.backup, self.target)
            log('restored previous toolchain after interrupted installation')

    def install_archive(self, archive):
        with tempfile.TemporaryDirectory(prefix='.go-stage-', dir=self.root/'.tools') as name:
            staging = Path(name)
            self.extract(archive, staging)
            fresh = staging/'go'
            if not self.healthy(fresh): raise RuntimeError('extracted Go toolchain failed exact version/architecture/completeness check')
            old = self.target.exists() or self.target.is_symlink()
            if old: os.replace(self.target, self.backup)
            try:
                os.replace(fresh, self.target)
                if not self.healthy(self.target): raise RuntimeError('installed Go health check failed')
            except BaseException:
                self.remove_tree(self.target)
                if old: os.replace(self.backup, self.target)
                raise
            self.remove_tree(self.backup)

    def run(self, explicit=None, offline=False, archive_only=False):
        (self.root/'.tools').mkdir(parents=True, exist_ok=True)
        report = {'schema_version':1,'status':'RUNNING','version':self.version,'architecture':self.arch,
                  'pinned_archive_sha256':self.expected,'scope':'TOOLCHAIN_BOOTSTRAP_ONLY','paper_result':False}
        # A contender must not overwrite the active installer's status file.
        with install_lock(self.root/'.tools/.go-bootstrap.lock'):
            try:
                self.recover()
                if explicit is None and not archive_only and self.healthy(self.target):
                    log(f'project-local Go {self.version} healthy; reusing it')
                    report.update(origin='EXISTING_TOOLCHAIN', archive_verified_this_invocation=False)
                else:
                    archive = self.obtain_archive(explicit, offline)
                    if not archive_only: self.install_archive(archive)
                    report.update(origin='VERIFIED_ARCHIVE', archive=str(archive), archive_verified_this_invocation=True)
                report['status']='PASS'
                log('[PASS] '+('archive ready' if archive_only else 'Go toolchain ready'))
                return report
            except BaseException as exc:
                report.update(status='FAIL', error_type=type(exc).__name__, error=str(exc))
                raise
            finally:
                report['events']=self.events
                dest = self.root/'.isolated/go-bootstrap.json';dest.parent.mkdir(parents=True, exist_ok=True)
                tmp=dest.with_suffix('.json.tmp');tmp.write_text(json.dumps(report,indent=2)+'\n');os.replace(tmp,dest)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-root', type=Path, default=ROOT)
    ap.add_argument('--archive', type=Path, help='local archive; still checked against the frozen SHA-256')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--archive-only', action='store_true')
    args=ap.parse_args()
    try:
        if platform.system()!='Linux': raise ValueError('Go bootstrap requires Linux')
        if os.environ.get('GO_VERSION',VERSION)!=VERSION:
            raise ValueError('this release pins Go '+VERSION+'; GO_VERSION override is not supported')
        Installer(args.source_root).run(args.archive,args.offline,args.archive_only)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,tarfile.TarError) as exc:
        print('[FAIL] Go bootstrap: '+str(exc),flush=True)
        raise SystemExit(2)

if __name__=='__main__':main()
