# LabelDoc - AI-Powered Legal Metrology Compliance Platform

Smart India Hackathon 2026 | Problem Statement: SIH26034
Team: Bitbandits | Theme: Miscellaneous

---

## What is LabelDoc?

LabelDoc is an AI-powered mobile inspection platform for Legal Metrology
officers to verify packaged commodity label compliance under the Legal
Metrology (Packaged Commodities) Rules, 2011.

---

## Key Features

- Multi-side label scanning (Front, Back, Side, Extra panels)
- Google Gemini Vision AI - multilingual OCR (Hindi + English)
- PCR 2011 Rule Engine - validates all 9 mandatory declarations
- Product Compliance - persistent product compliance record
- Drift Detection - detects what changed between inspections
- Risk-Based Prioritisation - ranks products HIGH / MEDIUM / LOW
- Court-ready PDF + editable DOCX reports with proof photograph
- Offline-first — works without internet, syncs to MongoDB

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React Native (Expo) |
| Backend | FastAPI + Uvicorn (Python) |
| AI Vision | Google Gemini 3.5 Flash |
| Database | MongoDB + Motor |
| Image Processing | Python PIL / Pillow |
| PDF Report | expo-print |
| DOCX Report | python-docx |
| Security | JWT + Role-Based Access |

---

## Project Structure

labeldoc/
├── frontend/ # React Native mobile app (Expo)
└── backend/ # FastAPI REST API + PCR Rule Engine
