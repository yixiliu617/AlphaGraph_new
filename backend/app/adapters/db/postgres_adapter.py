from sqlalchemy.orm import Session
from typing import List, Optional, Any, Dict
import hashlib
import uuid
from backend.app.interfaces.db_repository import DBRepository
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.thesis_ledger import ThesisLedger
from backend.app.models.orm.fragment_orm import FragmentORM, RecipeORM
from backend.app.models.orm.universe_orm import PublicCompanyORM, UserCompanyORM
from backend.app.models.domain.universe import PublicCompany, UserCompany


def compute_fragment_fingerprint(tenant_id: str, source_document_id: str, exact_location: str) -> str:
    """
    Returns a stable SHA-256 fingerprint for a fragment.

    The fingerprint uniquely identifies one extracted unit:
      - tenant_id         — isolates tenants from each other
      - source_document_id — UUID5 seeded from the PDF filename; same file always
                             produces the same ID regardless of when it was ingested
      - exact_location    — page range ("pp. 1-3") or page ("p. 3") within that file

    Used both for deduplication checks before insert and by the cleanup script.
    """
    raw = f"{tenant_id}:{source_document_id}:{exact_location}"
    return hashlib.sha256(raw.encode()).hexdigest()

class PostgresAdapter(DBRepository):
    """
    ADAPTER: Concrete implementation for PostgreSQL using SQLAlchemy.
    Fulfills the DBRepository port contract.
    """
    def __init__(self, session: Session):
        self.session = session
        print("Postgres Adapter initialized with SQLAlchemy session.")

    def save_fragment(self, fragment: DataFragment) -> bool:
        try:
            # --- Deduplication check -----------------------------------------
            metrics = fragment.content.get("extracted_metrics", {}) if isinstance(fragment.content, dict) else {}
            source_doc_id = metrics.get("source_document_id") or str(fragment.fragment_id)
            fingerprint = compute_fragment_fingerprint(
                fragment.tenant_id, source_doc_id, fragment.exact_location
            )
            existing = (
                self.session.query(FragmentORM)
                .filter(FragmentORM.content_fingerprint == fingerprint)
                .first()
            )
            if existing:
                print(
                    f"[DB] Skipping duplicate fragment: "
                    f"doc={source_doc_id[:8]} loc={fragment.exact_location} "
                    f"(existing id={existing.fragment_id[:8]})"
                )
                return True   # treat as success — data is already stored
            # -----------------------------------------------------------------

            orm_fragment = FragmentORM(
                fragment_id=str(fragment.fragment_id),
                tenant_id=fragment.tenant_id,
                tenant_tier=fragment.tenant_tier,
                lineage=fragment.lineage,
                source_type=fragment.source_type,
                source=fragment.source,
                exact_location=fragment.exact_location,
                reason_for_extraction=fragment.reason_for_extraction,
                content=fragment.content,
                content_fingerprint=fingerprint,
                created_at=fragment.created_at,
            )
            self.session.add(orm_fragment)
            self.session.commit()
            return True
        except Exception as e:
            print(f"Postgres Save Fragment Error: {e}")
            self.session.rollback()
            return False

    def get_fragment(self, fragment_id: uuid.UUID) -> Optional[DataFragment]:
        orm_fragment = self.session.query(FragmentORM).filter(FragmentORM.fragment_id == str(fragment_id)).first()
        if orm_fragment:
            return DataFragment(**orm_fragment.__dict__)
        return None

    def get_tenant_fragments(self, tenant_id: str, limit: int = 50) -> List[DataFragment]:
        orm_fragments = self.session.query(FragmentORM).filter(FragmentORM.tenant_id == tenant_id).limit(limit).all()
        return [DataFragment(**f.__dict__) for f in orm_fragments]

    def save_recipe(self, recipe: ExtractionRecipe) -> bool:
        try:
            orm_recipe = RecipeORM(
                recipe_id=str(recipe.recipe_id),
                tenant_id=recipe.tenant_id,
                name=recipe.name,
                target_sectors=recipe.target_sectors,
                version=str(recipe.version),
                ingestor_type=recipe.ingestor_type,
                llm_prompt_template=recipe.llm_prompt_template,
                expected_schema=recipe.expected_schema,
                is_public=str(recipe.is_public),
                created_at=recipe.created_at
            )
            self.session.add(orm_recipe)
            self.session.commit()
            return True
        except Exception as e:
            print(f"Postgres Save Recipe Error: {e}")
            self.session.rollback()
            return False

    def get_recipe(self, recipe_id: uuid.UUID) -> Optional[ExtractionRecipe]:
        orm_recipe = self.session.query(RecipeORM).filter(RecipeORM.recipe_id == str(recipe_id)).first()
        if orm_recipe:
            return ExtractionRecipe(**orm_recipe.__dict__)
        return None

    def get_ledger(self, tenant_id: str) -> Optional[ThesisLedger]:
        return None

    def update_ledger(self, ledger: ThesisLedger) -> bool:
        return False

    # --- UNIVERSE MANAGEMENT ---
    
    def get_public_company(self, ticker: str) -> Optional[PublicCompany]:
        orm_company = self.session.query(PublicCompanyORM).filter(PublicCompanyORM.ticker == ticker).first()
        if orm_company:
            return PublicCompany(
                ticker=orm_company.ticker,
                name=orm_company.name,
                gics_sector=orm_company.gics_sector,
                gics_subsector=orm_company.gics_subsector,
                gics_subindustry=orm_company.gics_subindustry,
                metadata=orm_company.company_metadata
            )
        return None

    def get_public_companies(self, sector: Optional[str] = None) -> List[PublicCompany]:
        query = self.session.query(PublicCompanyORM)
        if sector:
            query = query.filter(PublicCompanyORM.gics_sector == sector)
        orm_companies = query.all()
        return [
            PublicCompany(
                ticker=c.ticker,
                name=c.name,
                gics_sector=c.gics_sector,
                gics_subsector=c.gics_subsector,
                gics_subindustry=c.gics_subindustry,
                metadata=c.company_metadata
            ) for c in orm_companies
        ]

    def get_user_universe(self, tenant_id: str) -> List[UserCompany]:
        orm_companies = self.session.query(UserCompanyORM).filter(UserCompanyORM.tenant_id == tenant_id).all()
        return [
            UserCompany(
                company_id=uuid.UUID(c.company_id),
                tenant_id=c.tenant_id,
                ticker=c.ticker,
                user_category_1=c.user_category_1,
                user_category_2=c.user_category_2,
                is_active=c.is_active
            ) for c in orm_companies
        ]

    def get_user_company_coverage(self, tenant_id: str, ticker: str) -> Optional[UserCompany]:
        c = self.session.query(UserCompanyORM).filter(
            UserCompanyORM.tenant_id == tenant_id,
            UserCompanyORM.ticker == ticker
        ).first()
        if c:
            return UserCompany(
                company_id=uuid.UUID(c.company_id),
                tenant_id=c.tenant_id,
                ticker=c.ticker,
                user_category_1=c.user_category_1,
                user_category_2=c.user_category_2,
                is_active=c.is_active
            )
        return None

    def save_public_company(self, company: PublicCompany) -> bool:
        try:
            existing = self.session.query(PublicCompanyORM).filter(PublicCompanyORM.ticker == company.ticker).first()
            if existing:
                existing.name = company.name
                existing.gics_sector = company.gics_sector
                existing.gics_subsector = company.gics_subsector
                existing.gics_subindustry = company.gics_subindustry
                existing.company_metadata = company.metadata
            else:
                orm_company = PublicCompanyORM(
                    ticker=company.ticker,
                    name=company.name,
                    gics_sector=company.gics_sector,
                    gics_subsector=company.gics_subsector,
                    gics_subindustry=company.gics_subindustry,
                    company_metadata=company.metadata
                )
                self.session.add(orm_company)
            self.session.commit()
            return True
        except Exception as e:
            print(f"Postgres Save Public Company Error: {e}")
            self.session.rollback()
            return False
