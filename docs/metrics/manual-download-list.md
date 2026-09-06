# Congressional Record volumes to download by hand

**Resolved 2026-09-05 against archive.org.** Plain OCR text, one URL each, no
API key, no rate limit, no circuit breaker — which is why fetching these by
hand now beats waiting for Step 5.

Two volumes per question, because the pre/post-vote boundary falls between them:

- the volume **ending just before** the decision carries the debate: `framing`
- the volume **containing** the decision carries the roll call: `vote_record`

Index volumes are excluded — they are page indexes, not debate text, and the
naive "closest volume" query picks them up.

Save into `backend/.cache/`, which is gitignored. Keep the archive.org
identifier in the filename: it is the provenance, and `source_documents`
stores it as `external_id`.

**Total: ~104 MB across 10 files.**

## us-medicare-1965 — decision 1965-04-08

- **Congressional Record March 24, 1965-April 6, 1965: Vol 111**
  starts 1965-03-24 — 14.6 MB — framing, before the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_march-24-1965-april-6-1965_111/sim_congressional-record-proceedings-and-debates_march-24-1965-april-6-1965_111_djvu.txt

- **Congressional Record April 7, 1965-April 27, 1965: Vol 111**
  starts 1965-04-07 — 14.6 MB — contains the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_april-7-1965-april-27-1965_111/sim_congressional-record-proceedings-and-debates_april-7-1965-april-27-1965_111_djvu.txt

## us-prohibition-1919 — decision 1919-01-16

- **Congressional Record 1919: Vol 58 Appendix**
  starts 1919-01-01 — 3.4 MB — framing, before the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_1919_58_appendix/sim_congressional-record-proceedings-and-debates_1919_58_appendix_djvu.txt

- **Congressional Record January 06-26, 1919: Vol 57**
  starts 1919-01-06 — 10.6 MB — contains the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-06-26-1919_57/sim_congressional-record-proceedings-and-debates_january-06-26-1919_57_djvu.txt

## us-interstate-highway-1956 — decision 1956-04-27

- **Congressional Record March 28-April 26, 1956: Vol 102**
  starts 1956-03-28 — 13.2 MB — framing, before the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_march-28-april-26-1956_102/sim_congressional-record-proceedings-and-debates_march-28-april-26-1956_102_djvu.txt

- **Congressional Record April 27-May 21, 1956: Vol 102**
  starts 1956-04-27 — 13.3 MB — contains the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_april-27-may-21-1956_102/sim_congressional-record-proceedings-and-debates_april-27-may-21-1956_102_djvu.txt

## us-clean-air-act-1970 — decision 1970-06-10

- **Congressional Record 91st Congress 2nd Session May 25 - June 3, 1970: Vol 116**
  starts 1970-05-25 — 14.5 MB — framing, before the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_may-25-june-3-1970_116/sim_congressional-record-proceedings-and-debates_may-25-june-3-1970_116_djvu.txt

- **Congressional Record 91st Congress 2nd Session June 4-12, 1970: Vol 116**
  starts 1970-06-04 — -0.0 MB — contains the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_june-4-12-1970_116/sim_congressional-record-proceedings-and-debates_june-4-12-1970_116_djvu.txt

## us-affordable-care-act-2010 — decision 2010-03-21

The scanned series runs 1873-01-01 to 2008-06-23, so this question is
out of range. It needs GovInfo `CREC` (the daily edition, 1994+), which
is a keyed API fetch — leave it for Step 5.

## us-income-tax-1913 — decision 1913-02-03

- **Congressional Record January 6, 1913-January 25, 1913: Vol 49**
  starts 1913-01-06 — 9.9 MB — framing, before the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-6-1913-january-25-1913_49/sim_congressional-record-proceedings-and-debates_january-6-1913-january-25-1913_49_djvu.txt

- **Congressional Record January 26, 1913-February 12, 1913: Vol 49**
  starts 1913-01-26 — 9.5 MB — contains the decision
  https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-26-1913-february-12-1913_49/sim_congressional-record-proceedings-and-debates_january-26-1913-february-12-1913_49_djvu.txt
