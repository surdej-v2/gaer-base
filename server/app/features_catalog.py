"""The code's registry of feature keys. The database decides the ring.

/admin/features → „Importér fra koden“ creates the keys that are here but not in the database, in
ring 1. It never overwrites a ring an admin has set. Wrap the UI in <FeatureGate feature="key"> (web/src/features.tsx).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogFeature:
    key: str
    title: str
    description: str
    area: str  # bruger · integration · admin · platform


CATALOG: tuple[CatalogFeature, ...] = (
    CatalogFeature("klip", "Klip", "Gem tekst og links som klip og brug dem i Office og udvidelsen", "bruger"),
    CatalogFeature("filer", "Filer", "Upload til Object Storage, download via links der udløber", "bruger"),
    CatalogFeature("passkey", "Passkey", "Passkey efter tre logins med engangskode", "bruger"),
    CatalogFeature("mobil", "Mobil-app", "PWA under /m med bundmenu, kamera-upload og klip", "bruger"),
    CatalogFeature("extension", "Browserudvidelse", "Sidepanel i Chrome og Edge, gem siden som klip", "integration"),
    CatalogFeature("office", "Office-tilføjelser", "Word, Excel, PowerPoint og Outlook i én router", "integration"),
    CatalogFeature("website", "Website /w", "Offentlige Markdown-sider på dansk og engelsk", "platform"),
    CatalogFeature("heatmaps", "Heatmaps", "Klik- og scroll-heatmaps pr. side", "admin"),
    CatalogFeature("gdpr-export", "GDPR-eksport", "Samlet eksport ved anmodning om indsigt", "admin"),
    CatalogFeature("gaer-sync", "Hent opdateringer", "Træk ændringer fra gaer-base ind i din fork", "platform"),
)
