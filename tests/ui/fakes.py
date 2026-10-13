from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

from tests.state.test_r2 import FakeR2

XMLNS = "http://s3.amazonaws.com/doc/2006-03-01/"


def listing(keys, url, page=1000):
    """R2's ListObjectsV2 answer, with a delimiter, for the keys a request's prefix selects."""
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    prefix, delimiter = query["prefix"], query["delimiter"]
    names = []
    for key in sorted(keys):
        if key.startswith(prefix):
            head, cut, _ = key[len(prefix) :].partition(delimiter)
            entry = ("prefix", prefix + head + cut) if cut else ("key", key)
            if entry not in names:
                names.append(entry)
    start = int(query.get("continuation-token", "0"))
    chunk, more = names[start : start + page], start + page < len(names)
    items = "".join(
        f"<CommonPrefixes><Prefix>{escape(value)}</Prefix></CommonPrefixes>"
        if kind == "prefix"
        else f"<Contents><Key>{escape(value)}</Key></Contents>"
        for kind, value in chunk
    )
    token = f"<NextContinuationToken>{start + page}</NextContinuationToken>" if more else ""
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{XMLNS}">'
        f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{token}{items}"
        "</ListBucketResult>"
    )
    return 200, {}, xml.encode()


class FakeBucket(FakeR2):
    """FakeR2, plus listings: a GET of the bucket itself."""

    def __call__(self, method, url, headers, body, timeout):
        if urlsplit(url).path.count("/") == 1:
            self.requests.append((method, url, dict(headers)))
            if self.script:
                step = self.script.pop(0)
                if isinstance(step, BaseException):
                    raise step
                return step, {}, b""
            return listing(self.objects, url)
        return super().__call__(method, url, headers, body, timeout)
