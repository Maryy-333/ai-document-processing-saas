# AI Document Processing SaaS

AI-powered invoice processing platform that extracts structured
data from business documents using PDF extraction, OCR, and LLMs,
with human verification before approval.

## Status

🚧 In Development

## Features

- Invoice upload
- PDF text extraction
- OCR fallback
- AI-powered structured extraction
- Schema validation
- Human review and approval
- CSV/Excel export
- Multi-tenant architecture
- Processing history

## Tech Stack

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Pydantic
- PyMuPDF
- PaddleOCR
- LLM API
- Streamlit
- Docker

## Architecture

Coming soon.

## Project Status

- [x] Architecture
- [x] Application foundation
- [x] Database models
- [x] Alembic migrations
- [ ] Document upload
- [ ] PDF extraction
- [ ] OCR
- [ ] AI extraction
- [ ] Human review
- [ ] Export
- [ ] Authentication
- [ ] Deployment

## License

TBD

<!-- 
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost 
\c invoice_extractor

uvicorn app.main:app --reload
-->