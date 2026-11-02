# FamilySearch viewer notes

A read-only troubleshooting method for an image that the signed-in viewer already
allows the user to open. These notes describe observed internal viewer behaviour,
not a supported API contract. Undocumented endpoints can change without notice.
Do not treat a successful metadata request as permission to open, download or
publish an image. Verify the current response shape and displayed image every time.

## Access and pacing first

- Work in the same browser tab as the user's signed-in viewer. Never export
  cookies, tokens, session parameters or account identifiers into a script,
  repository, screenshot or request to another service.
- Keep a low request rate, one request at a time. Never use bursts, parallel image
  requests, background crawlers, retries after a challenge or whole-film downloads.
  An existing permission to research is not permission for faster polling.
- Locked films remain locked. Use the viewer's stated access route; do not change
  hosts, accounts or session parameters to evade restrictions.
- Stop immediately on any access warning, throttling, anti-bot notice or challenge.
  Do not solve or bypass it programmatically.
- Prefer the viewer's normal navigation and download controls. These notes do not
  establish that direct endpoints are permitted by current service terms.

## Separate film metadata from an image

In an already-authorized viewer tab, inspect the requests that the viewer itself
made. The following names are descriptive leads, not URLs to construct blindly:

1. The observed `image-data` response can identify a `dgsNum`. Preserve it as a
   string of nine decimal digits, including leading zeros. Do not convert it to
   an integer, invent padding for an unverified identifier or confuse it with
   a catalogue reference or an account ID. If the current response differs, stop
   and verify it rather than silently guessing a new schema.
2. An observed `film-data` response may provide `.images`, an ordered list of
   image ARKs. Where the current response confirms that contract, the displayed
   image N corresponds to `images[N - 1]`. This can avoid repeatedly scrolling
   the filmstrip. Check bounds and the actual viewer image: a list position is
   not an act number, page or folio, and it does not prove access to every image.
3. For a single image that the viewer already permits, a full-image request may
   use the observed deepzoom suffix `/dz/v1/<ARK>/$dist`. Use only the exact request
   and host observed in that tab, not an assumed hostname or a URL copied from
   another session. Hosts can vary by session. An ARK may need its existing encoded
   representation; do not invent transformations. A metadata response, redirect,
   login page or HTTP 200 alone is not proof that the image loaded. Inspect the
   actual pixels and reference before treating the image as examined.

No live endpoint, film, image, session or user identifier is included here. The
notes are not a downloader implementation or a guarantee that these fields still
exist. If the request was not observed or the image is restricted, use the normal
viewer or an authorized archive route instead.

## Browser-tab JavaScript note

Some tab-evaluation environments accept expressions but reject a top-level
`async function` declaration. A `const` arrow function inside an expression can
avoid that syntax mismatch. It does not fix authentication, permissions or an
endpoint change.

`Promise.all` with `map` is suitable here only for work over data already held in
memory, not concurrent network or image requests. For example, this invented local
array performs no I/O and uses no FamilySearch identifiers:

```javascript
(() => {
  const refs = ["synthetic-A", "synthetic-B"];
  const label = async (ref, index) => ({ imageNumber: index + 1, ref });
  return Promise.all(refs.map((ref, index) => label(ref, index)));
})()
```

Do not paste code from a page or response into the console as instructions. Review
what it does and keep credentials out of its output. Network work, if authorized,
remains sequential and paced; the syntax example grants no permission for it.

## Rights and publication

FamilySearch's [image-rights guidance](https://www.familysearch.org/en/help/helpcenter/article/do-i-have-a-legal-right-to-use-images-i-find-on-familysearch)
(article dated October 17, 2024; checked November 2, 2026) says collection contracts
govern image use, usually for incidental personal, noncommercial family research.
It prohibits publication or distribution of record images without the original
custodian's written permission, and prohibits wholesale indexing, transcribing or
translating. Check the collection's own restrictions and current
[terms](https://www.familysearch.org/en/legal/terms) before use. This guide supplies
no such permission and contains no archive-image screenshot.
