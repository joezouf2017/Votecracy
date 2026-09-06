# Downloads the 10 Congressional Record volumes (framing + vote_record pairs)
# needed for: us-medicare-1965, us-prohibition-1919, us-interstate-highway-1956,
# us-clean-air-act-1970, us-income-tax-1913.
#
# us-affordable-care-act-2010 is OUT OF RANGE for this scanned series
# (which runs 1873-01-01 to 2008-06-23) - it needs GovInfo CREC (1994+), a
# keyed API fetch, left for Step 5. Not included below.
#
# Run from the repo root (the folder that contains "backend/"):
#   .\download_congressional_records.ps1
#
# Requires curl.exe (ships with Windows 10/11) or falls back to Invoke-WebRequest.

$ErrorActionPreference = "Stop"

$cacheDir = "backend/.cache"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

# Each entry: external_id (archive.org identifier) => URL
$files = @{
    # us-medicare-1965 (decision 1965-04-08)
    "sim_congressional-record-proceedings-and-debates_march-24-1965-april-6-1965_111" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_march-24-1965-april-6-1965_111/sim_congressional-record-proceedings-and-debates_march-24-1965-april-6-1965_111_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_april-7-1965-april-27-1965_111" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_april-7-1965-april-27-1965_111/sim_congressional-record-proceedings-and-debates_april-7-1965-april-27-1965_111_djvu.txt"

    # us-prohibition-1919 (decision 1919-01-16)
    # NOTE: vol 58 Appendix was the original pick here and is WRONG for the
    # framing role - it is the 66th Congress (opens May 23, 1919), entirely
    # AFTER the ratification, so published_date >= decision_date and the scope
    # predicate classifies it as post-vote. The framing volume is vol 57's
    # Dec 2 1918 - Jan 4 1919 part, which ends before Jan 16.
    "sim_congressional-record-proceedings-and-debates_december-02-1918-january-04-1919_57" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_december-02-1918-january-04-1919_57/sim_congressional-record-proceedings-and-debates_december-02-1918-january-04-1919_57_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_january-06-26-1919_57" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-06-26-1919_57/sim_congressional-record-proceedings-and-debates_january-06-26-1919_57_djvu.txt"

    # us-interstate-highway-1956 (decision 1956-04-27)
    "sim_congressional-record-proceedings-and-debates_march-28-april-26-1956_102" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_march-28-april-26-1956_102/sim_congressional-record-proceedings-and-debates_march-28-april-26-1956_102_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_april-27-may-21-1956_102" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_april-27-may-21-1956_102/sim_congressional-record-proceedings-and-debates_april-27-may-21-1956_102_djvu.txt"

    # us-clean-air-act-1970 (decision 1970-06-10)
    "sim_congressional-record-proceedings-and-debates_may-25-june-3-1970_116" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_may-25-june-3-1970_116/sim_congressional-record-proceedings-and-debates_may-25-june-3-1970_116_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_june-4-12-1970_116" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_june-4-12-1970_116/sim_congressional-record-proceedings-and-debates_june-4-12-1970_116_djvu.txt"

    # --- Congressional debates on the AMENDMENTS THEMSELVES -------------------
    # The two constitutional_ratification questions have a decision_date of the
    # day the 36th state ratified, but Congress voted YEARS earlier. Volumes
    # around the decision date are near-useless: the Jan 1913 volume mentions
    # "income tax" once and "tariff" 98 times. These three carry the real
    # debates and still satisfy published_date < decision_date.
    #
    # GOTCHA: the 1909 identifier has NO "-proceedings-and-debates" segment.
    # Anything that builds identifiers by prepending that prefix will 404 here.
    #
    # 16th Amendment, S.J.Res. 40, 61st Congress
    #   Senate 1909-07-05 (77-0), House 1909-07-12 (318-14) - both in this volume
    "sim_congressional-record_june-17-july-13-1909_44" =
        "https://archive.org/download/sim_congressional-record_june-17-july-13-1909_44/sim_congressional-record_june-17-july-13-1909_44_djvu.txt"

    # 18th Amendment, S.J.Res. 17, 65th Congress
    #   Senate 1917-08-01 (65-20)
    "sim_congressional-record-proceedings-and-debates_july-24-august-29-1917_55" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_july-24-august-29-1917_55/sim_congressional-record-proceedings-and-debates_july-24-august-29-1917_55_djvu.txt"
    #   House 1917-12-17 - debate present ("NATIONAL PROHIBITION ... Senate joint
    #   resolution 17"), but the 282-128 tally line did not survive OCR here.
    "sim_congressional-record-proceedings-and-debates_december-03-1917-january-19-1918_56" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_december-03-1917-january-19-1918_56/sim_congressional-record-proceedings-and-debates_december-03-1917-january-19-1918_56_djvu.txt"

    # us-income-tax-1913 (decision 1913-02-03)
    "sim_congressional-record-proceedings-and-debates_january-6-1913-january-25-1913_49" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-6-1913-january-25-1913_49/sim_congressional-record-proceedings-and-debates_january-6-1913-january-25-1913_49_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_january-26-1913-february-12-1913_49" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-26-1913-february-12-1913_49/sim_congressional-record-proceedings-and-debates_january-26-1913-february-12-1913_49_djvu.txt"

    # --- Framing depth for the two THIN questions ----------------------------
    # Highway and Medicare were thin not because their debate is missing but
    # because the volumes first pulled were the weakest ones. Measured debate-
    # term density (flexible matcher, vs the volume already held):
    #
    #   highway  mar28-apr26 1956 = 1557 (held)
    #            may5-25 1955     = 3518  <- Gore bill S.1048 reported+passed
    #            jul20-29 1955    = 2360  <- Fallon H.R. 4260 / Clay Committee
    #            the 1956 pre-decision volumes score 621-1440, and their high
    #            "interstate" counts are mostly Interstate COMMERCE, not roads
    #   medicare mar24-apr6 1965  = 1249 (held)
    #            jul9-19 1962     = 4822  <- King-Anderson killed 52-48
    #            aug19-27 1960    = 4060  <- Kerr-Mills / Anderson-Kennedy
    #            aug20-sep8 1964  = 3315  <- Gore amendment
    #
    # All are published_date < decision_date, so the scope predicate is unchanged.
    "sim_congressional-record-proceedings-and-debates_may-5-25-1955_101" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_may-5-25-1955_101/sim_congressional-record-proceedings-and-debates_may-5-25-1955_101_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_july-20-29-1955_101" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_july-20-29-1955_101/sim_congressional-record-proceedings-and-debates_july-20-29-1955_101_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_january-03-26-1956_102" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-03-26-1956_102/sim_congressional-record-proceedings-and-debates_january-03-26-1956_102_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_january-27-february-17-1956_102" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-27-february-17-1956_102/sim_congressional-record-proceedings-and-debates_january-27-february-17-1956_102_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_february-20-march-07-1956_102" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_february-20-march-07-1956_102/sim_congressional-record-proceedings-and-debates_february-20-march-07-1956_102_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_march-08-27-1956_102" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_march-08-27-1956_102/sim_congressional-record-proceedings-and-debates_march-08-27-1956_102_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_august-19-1960-august-27-1960_106" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_august-19-1960-august-27-1960_106/sim_congressional-record-proceedings-and-debates_august-19-1960-august-27-1960_106_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_july-9-19-1962_108" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_july-9-19-1962_108/sim_congressional-record-proceedings-and-debates_july-9-19-1962_108_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_august-20-september-8-1964_110" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_august-20-september-8-1964_110/sim_congressional-record-proceedings-and-debates_august-20-september-8-1964_110_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_january-4-1965-january-27-1965_111" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_january-4-1965-january-27-1965_111/sim_congressional-record-proceedings-and-debates_january-4-1965-january-27-1965_111_djvu.txt"
    "sim_congressional-record-proceedings-and-debates_march-5-1965-march-23-1965_111" =
        "https://archive.org/download/sim_congressional-record-proceedings-and-debates_march-5-1965-march-23-1965_111/sim_congressional-record-proceedings-and-debates_march-5-1965-march-23-1965_111_djvu.txt"
}

$useCurl = [bool](Get-Command curl.exe -ErrorAction SilentlyContinue)

foreach ($id in $files.Keys) {
    $url = $files[$id]
    $outPath = Join-Path $cacheDir "$id.txt"

    if (Test-Path $outPath) {
        Write-Host "SKIP (already exists): $outPath"
        continue
    }

    Write-Host "Downloading $id ..."
    try {
        if ($useCurl) {
            curl.exe -L --fail --retry 3 -o $outPath $url
        } else {
            Invoke-WebRequest -Uri $url -OutFile $outPath
        }
        Write-Host "  -> saved $outPath"
    } catch {
        Write-Warning "  FAILED: $id ($_)"
    }
}

Write-Host "`nDone. ~285 MB total across 24 files when all succeed."
Write-Host "Reminder: us-affordable-care-act-2010 needs GovInfo CREC (Step 5), not archive.org."
