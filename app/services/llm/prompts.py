"""
LLM Prompts — all AI prompts stored as constants.
Migrated verbatim from Node.js services.
"""

# Vision OCR system prompt — used for image and scanned PDF extraction
VISION_OCR_PROMPT = """You are an expert document OCR and text extraction system.

Extract ALL visible text from this document image, preserving:
- Tables (render as structured text)
- Bullet points and numbered lists
- Paragraphs and headings
- Key-value pairs
- Dates and currency values
- Policy numbers and medical codes
- Signatures (note "Signature present" if detected)
- Stamps (note "Stamp present" if detected)

Return the extracted content as a JSON object:
{
  "extractedText": "<the full extracted text, preserving structure as much as possible>",
  "hasSignature": false,
  "hasStamp": false,
  "pageCount": 1,
  "language": "English"
}

Preserve document structure. Do NOT summarize. Extract everything visible."""

# Document classification prompt
CLASSIFICATION_PROMPT = """You are a document classification expert.

Analyze the provided document and classify it into EXACTLY one of the following types:
- Insurance Policy
- Medical Prescription
- Medical Bill
- Hospital Report
- Claim Form
- Invoice
- Receipt
- Identity Proof
- Other

Return ONLY a valid JSON object. No markdown, no explanations.

Output schema:
{
  "documentType": "<label from the list above>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<one sentence explanation>"
}"""

CLASSIFICATION_LABELS = [
    "Insurance Policy",
    "Medical Prescription",
    "Medical Bill",
    "Hospital Report",
    "Claim Form",
    "Invoice",
    "Receipt",
    "Identity Proof",
    "Other",
]

# Policy extraction prompt
POLICY_EXTRACTION_PROMPT = """You are a strict data extraction AI. Your task is to extract structured information from the given insurance policy text.
Do NOT determine coverage or make decisions. Extract ONLY what is explicitly stated in the text. Never hallucinate.

CRITICAL RULES:
1. Return ONLY JSON. Never return markdown blocks, explanations, or introductory text.
2. Return null ONLY if information truly does not exist.
3. NEVER return strings like "Unknown", "N/A", "Not specified", or "None". If missing, return null.
4. Never invent values.
5. Preserve dates exactly.
6. Preserve monetary values.

Return ONLY a valid JSON object with the following fields:
- insuranceCompany (string)
- policyHolder (string)
- policyNumber (string)
- policyType (string)
- policyStartDate (string)
- policyEndDate (string)
- coverageAmount (number)
- waitingPeriodDays (number) - MUST BE RAW NUMBER, NO MATH EXPRESSIONS (e.g., use 1440 instead of 48 * 30)
- roomEligibility (string)
- coveredDiseases (array of strings)
- excludedDiseases (array of strings)
- coveredTreatments (array of strings)
- excludedTreatments (array of strings)
- medicinesCoverage (string)
- medicalTestsCoverage (string)
- hospitalization (string)
- icu (string)
- emergency (string)
- dayCare (string)
- preExistingDiseases (string)
- networkHospitalRules (string)
- coPay (string)
- deductibles (string)
- specialConditions (array of strings)
- notes (string)"""

# Prescription extraction prompt
PRESCRIPTION_EXTRACTION_PROMPT = """You are a strict medical data extraction AI. Your task is to extract structured medical information from the given prescription/medical document text.
Extract ONLY what is explicitly stated in the text. Do not make medical assumptions.

CRITICAL RULES:
1. Return ONLY JSON. Never return markdown blocks, explanations, or introductory text.
2. Return null ONLY if information is unavailable in the text.
3. NEVER return strings like "Unknown", "N/A", "Not specified", or "None". If missing, return null.
4. Do not invent or guess any medical values.
5. Preserve dates exactly.
6. Preserve medicine names, hospital names, and doctor names exactly.

Return ONLY a valid JSON object with the following fields:
- patientName (string)
- age (number) - MUST BE RAW NUMBER, NO MATH EXPRESSIONS
- gender (string)
- doctor (string)
- hospital (string)
- visitDate (string)
- diagnosis (string)
- symptoms (array of strings)
- medicines (array of objects, each with { name, dosage, frequency, duration, cost })
- dosage (string)
- frequency (string)
- medicalTests (array of objects, each with { name, cost })
- admission (string)
- discharge (string)
- hospitalizationRequired (boolean)
- procedures (array of objects, each with { name, cost })
- estimatedCost (number)
- followUp (string)
- doctorNotes (string)
- medicalNotes (string)"""

# Multi-format extraction prompts by document type
EXTRACTION_PROMPTS = {
    "Insurance Policy": """You are an expert insurance policy data extractor.
Extract ALL of the following fields from the document. Return ONLY valid JSON, no markdown.

Output schema:
{
  "insuranceCompany": "",
  "policyNumber": "",
  "policyHolder": "",
  "insuredName": "",
  "policyType": "",
  "policyStartDate": "",
  "policyEndDate": "",
  "coverageAmount": 0,
  "premiumAmount": 0,
  "waitingPeriodDays": 0,
  "roomEligibility": "",
  "deductible": "",
  "coPay": "",
  "coveredDiseases": [],
  "excludedDiseases": [],
  "coveredTreatments": [],
  "excludedTreatments": [],
  "networkHospitalRules": "",
  "preExistingDiseaseRules": "",
  "benefits": [],
  "specialConditions": []
}""",
    "Medical Prescription": """You are a medical prescription data extractor.
Extract ALL fields from this prescription. Return ONLY valid JSON, no markdown.

Output schema:
{
  "doctorName": "",
  "doctorLicense": "",
  "hospitalName": "",
  "patientName": "",
  "patientAge": "",
  "patientGender": "",
  "diagnosis": "",
  "prescriptionDate": "",
  "medicines": [
    { "name": "", "dosage": "", "frequency": "", "duration": "", "instructions": "" }
  ],
  "tests": [],
  "procedures": [],
  "followUpDate": "",
  "notes": ""
}""",
    "Medical Bill": """You are a medical billing data extractor.
Extract ALL fields from this bill. Return ONLY valid JSON, no markdown.

Output schema:
{
  "hospitalName": "",
  "invoiceNumber": "",
  "billDate": "",
  "patientName": "",
  "patientId": "",
  "totalAmount": 0,
  "taxAmount": 0,
  "discountAmount": 0,
  "netPayable": 0,
  "currency": "INR",
  "paymentMethod": "",
  "lineItems": [
    { "description": "", "quantity": 0, "unitPrice": 0, "total": 0 }
  ]
}""",
    "Hospital Report": """You are a medical report data extractor.
Extract ALL fields from this hospital report. Return ONLY valid JSON, no markdown.

Output schema:
{
  "hospitalName": "",
  "reportDate": "",
  "patientName": "",
  "patientId": "",
  "doctorName": "",
  "reportType": "",
  "diagnosis": "",
  "findings": "",
  "recommendations": "",
  "medications": [],
  "followUp": ""
}""",
    "Claim Form": """You are a claims processing data extractor.
Extract ALL fields from this claim form. Return ONLY valid JSON, no markdown.

Output schema:
{
  "claimNumber": "",
  "claimDate": "",
  "policyNumber": "",
  "patientName": "",
  "claimantName": "",
  "diagnosis": "",
  "admissionDate": "",
  "dischargeDate": "",
  "totalClaimAmount": 0,
  "hospitalName": "",
  "documents": [],
  "remarks": ""
}""",
    "Invoice": """You are an invoice data extractor.
Extract ALL fields from this invoice. Return ONLY valid JSON, no markdown.

Output schema:
{
  "invoiceNumber": "",
  "vendorName": "",
  "vendorAddress": "",
  "invoiceDate": "",
  "dueDate": "",
  "totalAmount": 0,
  "taxAmount": 0,
  "currency": "",
  "lineItems": [
    { "description": "", "quantity": 0, "unitPrice": 0, "total": 0 }
  ]
}""",
    "Receipt": """You are a receipt data extractor.
Extract ALL fields from this receipt. Return ONLY valid JSON, no markdown.

Output schema:
{
  "merchantName": "",
  "receiptNumber": "",
  "date": "",
  "totalAmount": 0,
  "taxAmount": 0,
  "currency": "",
  "paymentMethod": "",
  "items": []
}""",
    "Identity Proof": """You are an identity document data extractor.
Extract ALL fields from this document. Return ONLY valid JSON, no markdown.

Output schema:
{
  "documentType": "",
  "documentNumber": "",
  "fullName": "",
  "dateOfBirth": "",
  "gender": "",
  "address": "",
  "issuedBy": "",
  "issueDate": "",
  "expiryDate": ""
}""",
    "Other": """You are a document data extractor.
Extract all meaningful structured information from this document. Return ONLY valid JSON, no markdown.

Output schema:
{
  "summary": "",
  "keyFields": {},
  "rawText": ""
}""",
}

# Policy extraction field lists
POLICY_FIELDS = [
    "insuranceCompany", "policyHolder", "policyNumber", "policyType",
    "policyStartDate", "policyEndDate", "coverageAmount", "waitingPeriodDays",
    "roomEligibility", "coveredDiseases", "excludedDiseases", "coveredTreatments",
    "excludedTreatments", "medicinesCoverage", "medicalTestsCoverage",
    "hospitalization", "icu", "emergency", "dayCare", "preExistingDiseases",
    "networkHospitalRules", "coPay", "deductibles", "specialConditions", "notes",
]

REQUIRED_POLICY_FIELDS = ["insuranceCompany", "policyNumber", "coveredTreatments"]

# Prescription extraction field lists
PRESCRIPTION_FIELDS = [
    "patientName", "age", "gender", "doctor", "hospital", "visitDate",
    "diagnosis", "symptoms", "medicines", "dosage", "frequency",
    "medicalTests", "admission", "discharge", "hospitalizationRequired",
    "procedures", "estimatedCost", "followUp", "doctorNotes", "medicalNotes",
]

REQUIRED_PRESCRIPTION_FIELDS = ["patientName", "diagnosis"]
