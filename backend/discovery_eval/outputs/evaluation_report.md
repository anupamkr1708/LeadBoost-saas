# Discovery Evaluation Report

Generated: 2026-08-04T04:41:02.576545+00:00

This report evaluates the production Discovery pipeline (`application.discovery.discovery_service.DiscoveryService`) as a black box, executed exactly up through ranking (the pipeline's own "Discovery Result" boundary), via its existing internal stage methods. No Lead was created, no database was written, and no downstream AI/scraper/enrichment pipeline ran for any query in this benchmark.

## Overall Summary

- **Discovery Health Score: 81.0 / 100**
- Queries run: 113 (113 parsed and executed, 0 failed to parse)
- Businesses found: 581
- Validated: 508 (87.4%)
- Rejected (validation failed): 65
- Duplicates removed: 8
- No website found: 65

## Latency Analysis

| Metric | Value (ms) |
|---|---|
| Average | 14667.8 |
| Median | 11950 |
| P95 | 42192.4 |
| P99 | 72482.8 |

See `latency.png` for the full distribution and per-stage breakdown.

## Validation Analysis

- Validation success rate: 87.4%
- Website resolution success rate: 88.8%
- Validation failure rate: 0.0%
- Duplicate rate: 1.4%

**Most common validation failures:**

| Reason | Count |
|---|---|
| unreachable_http_403 | 32 |
| no_verified_website | 19 |
| connection_error: Retryable status 429 | 6 |
| request_timed_out | 5 |
| connection_error: 400, message="Got more than 8190 bytes when reading: b'font-src fonts.gstatic.com use.typekit.net https://cdnjs.cloudflare.com *.fontawesome.com https://fo...'.", url='https://www.royaloakindia.com/?srsltid=AfmBOoqITgq7wFJg3pDbKKozZbIzgwtrETTGnEXtf__q-HrGp8brULxx' | 1 |
| connection_error: Cannot connect to host www.godeepak.com:443 ssl:True [SSLCertVerificationError: (1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)')] | 1 |
| connection_error: Cannot connect to host www.cafecoffeeday.com:443 ssl:True [SSLCertVerificationError: (1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)')] | 1 |

## Provider Analysis

- Provider agreement rate: 96.4%

**Candidates contributed per provider (`BusinessCandidate.source`):**

| Provider | Candidates |
|---|---|
| serper_business_search | 581 |

See `provider_performance.png` for a visual breakdown.

## Ranking Analysis

- Average winner confidence: 0.692
- Average competition margin: 0.012
- Queries with multiple strong candidates (top-2 rank scores within 5.0 points): 56
- Queries where the winner had no rival candidate at all (`compete()` had exactly one domain to evaluate -- margin is 0.0 by definition, not a close race): 106

## Identity Analysis

- Average identity stability: 0.596
- Selected businesses resolving to a directory/marketplace domain: 4
- Selected businesses resolving to a social-profile domain: 0
- Selected businesses carrying a structural false-positive signal: 309

## Most Common Failure Reasons

| Reason | Count |
|---|---|
| unreachable_http_403 | 32 |
| no_verified_website | 19 |
| connection_error: Retryable status 429 | 6 |
| request_timed_out | 5 |
| duplicate of 'domain:historyofvadodara.in' | 2 |
| duplicate of 'domain:metroshoes.com' | 1 |
| connection_error: 400, message="Got more than 8190 bytes when reading: b'font-src fonts.gstatic.com use.typekit.net https://cdnjs.cloudflare.com *.fontawesome.com https://fo...'.", url='https://www.royaloakindia.com/?srsltid=AfmBOoqITgq7wFJg3pDbKKozZbIzgwtrETTGnEXtf__q-HrGp8brULxx' | 1 |
| duplicate of 'domain:thechennaimobiles.com' | 1 |
| duplicate of 'domain:sirtbhopal.ac.in' | 1 |
| connection_error: Cannot connect to host www.godeepak.com:443 ssl:True [SSLCertVerificationError: (1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)')] | 1 |

## Queries That Need Manual Review

- `retail-01` — "shoe stores in Mumbai"
- `retail-02` — "sports shops in Delhi"
- `retail-03` — "electronics stores in Bengaluru"
- `health-01` — "eye hospitals in Patna"
- `health-04` — "physiotherapy clinics in Kochi"
- `health-05` — "diagnostic centres in Lucknow"
- `health-06` — "hospitals in Patna for eye treatment"
- `food-02` — "cafes in Gurgaon"
- `food-03` — "pizza places in Noida"
- `food-05` — "ice cream shops in Ahmedabad"
- `food-06` — "best 15 restaurants near Mumbai Airport"
- `edu-01` — "schools in Mysore"
- `edu-02` — "engineering colleges in Bhopal"
- `edu-03` — "coaching institutes in Kota"
- `edu-04` — "music schools in Kolkata"
- `edu-05` — "language institutes in Delhi"
- `edu-06` — "green valley school Dehradun"
- `tech-02` — "AI startups in Hyderabad"
- `tech-04` — "IT consulting firms in Chennai"
- `tech-06` — "pioneer technologies Hyderabad"
- `prof-02` — "law firms in Delhi"
- `prof-03` — "architects in Ahmedabad"
- `prof-04` — "digital marketing agencies in Bengaluru"
- `prof-06` — "united traders Delhi"
- `re-01` — "real estate developers in Noida"
- `re-02` — "property consultants in Gurgaon"
- `re-04` — "interior designers in Jaipur"
- `re-05` — "coworking spaces in Hyderabad"
- `mfg-03` — "steel manufacturers in Jamshedpur"
- `mfg-04` — "chemical companies in Vadodara"
- `mfg-06` — "industrial equipment suppliers in Faridabad"
- `hosp-04` — "tour operators in Leh"
- `hosp-06` — "top 5 hotels in Goa"
- `gov-02` — "municipal corporation offices in Nagpur"
- `gov-04` — "district collector office in Patna"
- `fin-01` — "banks in Chandigarh"
- `fin-03` — "investment advisors in Mumbai"
- `fin-04` — "microfinance companies in Ranchi"
- `ngo-04` — "old age homes in Coimbatore"
- `brand-01` — "McDonald's outlets in Mumbai"
- `brand-04` — "Cafe Coffee Day outlets in Hyderabad"
- `brand-05` — "Big Bazaar stores in Ahmedabad"
- `local-03` — "Prime cuts butcher Jodhpur"
- `local-04` — "Om electricals Amritsar"
- `amb-01` — "regal shoes Mumbai"
- `amb-03` — "woodland Bangalore"
- `amb-05` — "national hospital Patna"
- `amb-06` — "lotus clinic Lucknow"
- `amb-08` — "star traders Rajkot"
- `alias-02` — "hospitals in Madras"
- `alias-03` — "restaurants in Calcutta"
- `alias-04` — "hotels in Poona"
- `alias-05` — "clinics in Trivandrum"
- `alias-06` — "textile mills in Baroda"
- `alias-07` — "colleges in Allahabad"
- `alias-08` — "resorts in Pondicherry"
- `mis-01` — "restarants in Indore"
- `mis-02` — "phsyiotherapy clinics in Kochi"
- `mis-03` — "electroncis stores in Bengaluru"
- `mis-05` — "enginering colleges in Bhopal"
- `nopre-02` — "Dental clinics Bangalore"
- `nopre-03` — "Real estate agents Kolkata"
- `prep-02` — "coffee shops around Pune"
- `filler-01` — "I need software companies near Bangalore"
- `word-02` — "Infosys Pune"
- `word-03` — "Titan showroom Chennai"

## Queries With Weak Competition (margin < 0.05)

None.

## Queries With Low Confidence (winner confidence < 0.50)

- `food-02` — "cafes in Gurgaon"
- `food-03` — "pizza places in Noida"
- `food-05` — "ice cream shops in Ahmedabad"
- `tech-02` — "AI startups in Hyderabad"
- `prof-06` — "united traders Delhi"
- `mfg-06` — "industrial equipment suppliers in Faridabad"
- `hosp-06` — "top 5 hotels in Goa"
- `local-03` — "Prime cuts butcher Jodhpur"
- `amb-01` — "regal shoes Mumbai"
- `amb-06` — "lotus clinic Lucknow"
- `amb-08` — "star traders Rajkot"
- `alias-02` — "hospitals in Madras"
- `mis-01` — "restarants in Indore"
- `mis-05` — "enginering colleges in Bhopal"
- `prep-02` — "coffee shops around Pune"

## Queries With No Website

- `brand-04` — "Cafe Coffee Day outlets in Hyderabad"
- `word-02` — "Infosys Pune"
- `word-03` — "Titan showroom Chennai"

## Queries Where The Winner Had No Rival Candidate (margin 0.0 by definition)

- `retail-01` — "shoe stores in Mumbai"
- `retail-02` — "sports shops in Delhi"
- `retail-03` — "electronics stores in Bengaluru"
- `retail-04` — "bookstores in Pune"
- `retail-05` — "furniture stores in Hyderabad"
- `retail-06` — "top 10 mobile phone shops in Chennai"
- `health-01` — "eye hospitals in Patna"
- `health-02` — "dentists in Jaipur"
- `health-03` — "cardiology hospitals in Chennai"
- `health-04` — "physiotherapy clinics in Kochi"
- `health-05` — "diagnostic centres in Lucknow"
- `health-06` — "hospitals in Patna for eye treatment"
- `food-01` — "restaurants in Indore"
- `food-02` — "cafes in Gurgaon"
- `food-03` — "pizza places in Noida"
- `food-04` — "bakeries in Surat"
- `food-05` — "ice cream shops in Ahmedabad"
- `food-06` — "best 15 restaurants near Mumbai Airport"
- `edu-01` — "schools in Mysore"
- `edu-03` — "coaching institutes in Kota"
- `edu-04` — "music schools in Kolkata"
- `edu-05` — "language institutes in Delhi"
- `edu-06` — "green valley school Dehradun"
- `tech-01` — "software companies in Bengaluru"
- `tech-02` — "AI startups in Hyderabad"
- `tech-03` — "cybersecurity companies in Pune"
- `tech-04` — "IT consulting firms in Chennai"
- `tech-05` — "cloud consulting companies in Gurugram"
- `tech-06` — "pioneer technologies Hyderabad"
- `prof-01` — "chartered accountants in Mumbai"
- `prof-02` — "law firms in Delhi"
- `prof-03` — "architects in Ahmedabad"
- `prof-04` — "digital marketing agencies in Bengaluru"
- `prof-05` — "recruitment agencies in Pune"
- `prof-06` — "united traders Delhi"
- `re-01` — "real estate developers in Noida"
- `re-02` — "property consultants in Gurgaon"
- `re-03` — "construction companies in Kochi"
- `re-04` — "interior designers in Jaipur"
- `re-05` — "coworking spaces in Hyderabad"
- `mfg-01` — "textile manufacturers in Surat"
- `mfg-02` — "automobile suppliers in Pune"
- `mfg-03` — "steel manufacturers in Jamshedpur"
- `mfg-04` — "chemical companies in Vadodara"
- `mfg-05` — "plastic manufacturers in Rajkot"
- `mfg-06` — "industrial equipment suppliers in Faridabad"
- `hosp-01` — "hotels in Goa"
- `hosp-02` — "resorts in Udaipur"
- `hosp-03` — "travel agencies in Delhi"
- `hosp-04` — "tour operators in Leh"
- `hosp-05` — "homestays in Coorg"
- `hosp-06` — "top 5 hotels in Goa"
- `gov-01` — "passport seva kendra in Lucknow"
- `gov-02` — "municipal corporation offices in Nagpur"
- `gov-03` — "regional transport office in Bhopal"
- `gov-04` — "district collector office in Patna"
- `fin-01` — "banks in Chandigarh"
- `fin-02` — "insurance agents in Indore"
- `fin-03` — "investment advisors in Mumbai"
- `fin-04` — "microfinance companies in Ranchi"
- `ngo-01` — "NGOs in Bhubaneswar"
- `ngo-02` — "child welfare organizations in Kolkata"
- `ngo-03` — "environmental NGOs in Dehradun"
- `ngo-04` — "old age homes in Coimbatore"
- `brand-01` — "McDonald's outlets in Mumbai"
- `brand-02` — "Domino's Pizza in Bengaluru"
- `brand-03` — "Reliance Trends stores in Pune"
- `local-01` — "Sharma general store Kanpur"
- `local-02` — "Sunrise bakery Nashik"
- `local-03` — "Prime cuts butcher Jodhpur"
- `local-04` — "Om electricals Amritsar"
- `local-05` — "City Xerox Aligarh"
- `amb-01` — "regal shoes Mumbai"
- `amb-02` — "apollo Chennai"
- `amb-03` — "woodland Bangalore"
- `amb-04` — "classic furniture Jaipur"
- `amb-05` — "national hospital Patna"
- `amb-06` — "lotus clinic Lucknow"
- `amb-07` — "modern bakery Pune"
- `amb-08` — "star traders Rajkot"
- `amb-09` — "royal enterprises Surat"
- `alias-01` — "software companies in Bombay"
- `alias-02` — "hospitals in Madras"
- `alias-03` — "restaurants in Calcutta"
- `alias-05` — "clinics in Trivandrum"
- `alias-06` — "textile mills in Baroda"
- `alias-08` — "resorts in Pondicherry"
- `mis-01` — "restarants in Indore"
- `mis-02` — "phsyiotherapy clinics in Kochi"
- `mis-03` — "electroncis stores in Bengaluru"
- `mis-04` — "chartered accountnats in Mumbai"
- `mis-05` — "enginering colleges in Bhopal"
- `nopre-01` — "Noida software companies"
- `nopre-02` — "Dental clinics Bangalore"
- `nopre-03` — "Real estate agents Kolkata"
- `nopre-04` — "Warangal steel traders"
- `prep-01` — "gyms near Delhi"
- `prep-02` — "coffee shops around Pune"
- `prep-03` — "law firms located in Chennai"
- `prep-04` — "pharmacies close to Lucknow"
- `filler-01` — "I need software companies near Bangalore"
- `filler-02` — "Find me the best 10 gyms in Delhi"
- `filler-03` — "Show me diagnostic centres in Lucknow"
- `word-01` — "Wipro Bengaluru"
- `word-04` — "Bata Kolkata"
- `word-05` — "small business consulting services in Rajkot"

## Discovery Health Score

**81.0 / 100** — unweighted mean of five already-measured rates from this run: validation success rate, website resolution success rate, average winner confidence, average identity stability, and query parse success rate. Not a Discovery-computed number; this is this evaluation framework's own rollup, for regression comparison across runs, not a judgment Discovery itself makes.

## Recommendations

Every recommendation below is triggered by a specific metric in this run crossing a fixed threshold (see `report.py::_recommendations`). None of these propose an architectural change -- per the evaluation brief, this framework only measures Discovery, it does not redesign it.

- 4 selected business(es) resolved to a domain matching this framework's independent directory/marketplace list, despite passing Discovery's own validation. Cross-check these against `application.discovery.website_validator.REJECTED_DOMAINS` for a possible gap (see `business_results.csv`, `audit_flags` contains `directory_or_marketplace_selected`).
- 309 selected business(es) carry a structural false-positive risk code from `false_positive.py` (visible in `False Positive Flags`) despite being the final selection -- these passed competition/ranking but were still flagged upstream; worth a manual look.
- 106 of 113 queries (93.8%) had only one website candidate to resolve identity against, so `winning_margin` is 0.0 by definition (see `competition.py::compete`'s own single-candidate branch), not a genuinely thin race. This usually means only one search provider contributed candidates this run -- check `provider_candidate_counts` above; if it's just one provider, treat this run's confidence/identity/false-positive numbers as a single-provider baseline, not a representative measurement of Discovery with all providers active.
- P95 latency is 42192ms. If this benchmark is run again after a Discovery change and this number moves up materially, treat it as a regression signal before merging.
- 66 of 113 queries (58.4% if agg['ok_queries'] else 0) are flagged for manual review (weak confidence, thin competition margin, provider disagreement, low identity stability, or no validated result). See `manual_review.csv`.
