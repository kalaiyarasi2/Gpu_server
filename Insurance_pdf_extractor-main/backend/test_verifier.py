from text_quality_verifier import TextQualityVerifier

sample_text = """
encova Policy Number: WCB1036546
From Date: 09/30/2020
Employer Loss Run Statement
NSURANCE Thru Date: 09/30/2023

Valuation Date: 05/13/2025

Seve asa ane | eens | eal es sinless)
ca Coste ft |__inermty | Renovoesl  s000[ 5000] _ souo]  =0
GlamNumber | Coverage [Name  | _ lass_ _| AasidontDate | Glamstaus | ___|_Indernty | eda! | Expense | Toll
[Seve asa arine | ean] eal olsen) ie) sass
ca tm ft | Inerty | Reaves! s000| S000]  =s0ma) = SCSC#0
famine [ Gow ane [assis [casas [iets [sat [se [Tort
se
aT irainortear [8 |_inderty | Reeves) _ s000[ sooo]  ~so0o]  =000
GlamNumber | Coverage [Name  | _ lass__ | AasidontDate | Glamstaus | ____|_Indernty | edkal | Expense | Tot
SSR Peeve NANRSFINNEaaTne | ORMTGBTTN| Pate! sives0000|_ _srvenrras|  __szeera|  __staoaaore
ca ioc ft |_inerty | Removes! s000| S000]  so0o] = 00

Pail s2ir7e)  s0605|  _straaara]  __soaness

Page 1 of 2
"""

verifier = TextQualityVerifier()
analysis = verifier.analyze_quality(sample_text, num_pages=2)

print(f"Is Acceptable: {analysis['is_acceptable']}")
print(f"Reason: {analysis['reason']}")
print(f"Metrics: {analysis['metrics']}")
