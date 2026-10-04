"""
Generate synthetic messy test data for the Single Source of Truth pipeline.
Creates CSV files for HR, Payroll, and Licenses, plus a PDF schedule.
The data has intentional inconsistencies: name variations, abbreviations,
typos, mismatched hours, and expiring/expired licenses.
"""

import csv
import os
import random
from datetime import date, timedelta

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data", "sample")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Ground truth employees
# ---------------------------------------------------------------------------

EMPLOYEES = [
    {
        "id": "E201", "first": "Sofia", "last": "Reyes",
        "title": "Registered Nurse", "code": "RN",
        "facility": "Bayside", "fac_code": "BYS",
        "phone": "5551234567", "license": "RN-551203",
        "lic_type": "Registered Nurse", "lic_exp": "2025-03-15",  # EXPIRED
        "hire": "2019-06-01",
    },
    {
        "id": "E202", "first": "James", "last": "Okonkwo",
        "title": "Certified Nursing Assistant", "code": "CNA",
        "facility": "Bayside", "fac_code": "BYS",
        "phone": "5559876543", "license": "CNA-884710",
        "lic_type": "CNA", "lic_exp": "2027-01-20",
        "hire": "2021-03-15",
    },
    {
        "id": "E203", "first": "Maria", "last": "Chen",
        "title": "Licensed Practical Nurse", "code": "LPN",
        "facility": "Riverdale", "fac_code": "RVD",
        "phone": "5554567890", "license": "LPN-339201",
        "lic_type": "Licensed Practical Nurse", "lic_exp": "2026-11-05",
        "hire": "2020-09-10",
    },
    {
        "id": "E204", "first": "David", "last": "Washington",
        "title": "Registered Nurse", "code": "RN",
        "facility": "Riverdale", "fac_code": "RVD",
        "phone": "5553216549", "license": "RN-772045",
        "lic_type": "Registered Nurse", "lic_exp": "2026-12-01",
        "hire": "2018-02-20",
    },
    {
        "id": "E205", "first": "Aisha", "last": "Patel",
        "title": "Director of Nursing", "code": "DON",
        "facility": "Bayside", "fac_code": "BYS",
        "phone": "5557778888", "license": "RN-610398",
        "lic_type": "Registered Nurse", "lic_exp": "2026-12-30",
        "hire": "2015-07-01",
    },
    {
        "id": "E206", "first": "Carlos", "last": "Mendez",
        "title": "Certified Nursing Assistant", "code": "CNA",
        "facility": "Bayside", "fac_code": "BYS",
        "phone": "5551112222", "license": "CNA-990145",
        "lic_type": "CNA", "lic_exp": "2026-11-15",
        "hire": "2022-01-10",
    },
    {
        "id": "E207", "first": "Jennifer", "last": "Nguyen",
        "title": "Physical Therapist", "code": "PT",
        "facility": "Riverdale", "fac_code": "RVD",
        "phone": "5553334444", "license": "PT-445501",
        "lic_type": "Physical Therapy", "lic_exp": "2027-05-20",
        "hire": "2020-11-01",
    },
    {
        "id": "E208", "first": "Robert", "last": "Kim",
        "title": "Registered Nurse", "code": "RN",
        "facility": "Riverdale", "fac_code": "RVD",
        "phone": "5556667777", "license": "RN-881234",
        "lic_type": "Registered Nurse",
        "lic_exp": (date.today() + timedelta(days=22)).isoformat(),  # EXPIRING SOON
        "hire": "2021-08-15",
    },
    {
        "id": "E209", "first": "Fatima", "last": "Al-Rashid",
        "title": "Social Worker", "code": "SW",
        "facility": "Bayside", "fac_code": "BYS",
        "phone": "5558889999", "license": "SW-223344",
        "lic_type": "Social Work", "lic_exp": "2027-09-01",
        "hire": "2023-04-01",
    },
    {
        "id": "E210", "first": "Marcus", "last": "Johnson",
        "title": "Certified Nursing Assistant", "code": "CNA",
        "facility": "Riverdale", "fac_code": "RVD",
        "phone": "5550001111", "license": "CNA-556677",
        "lic_type": "CNA", "lic_exp": "2027-02-28",
        "hire": "2022-06-15",
    },
]


# ---------------------------------------------------------------------------
# Messiness functions
# ---------------------------------------------------------------------------

def messy_name_hr(emp):
    """HR has clean names."""
    return emp["first"], emp["last"]


def messy_name_payroll(emp):
    """Payroll uses LAST, FIRST format, sometimes all-caps, sometimes typos."""
    first = emp["first"]
    last = emp["last"]
    variants = [
        f"{last.upper()}, {first.upper()}",
        f"{last}, {first}",
        f"{last.upper()}, {first}",
    ]
    # Occasional typo
    if random.random() < 0.2:
        name = random.choice(variants)
        pos = random.randint(1, len(name) - 2)
        name = name[:pos] + name[pos].swapcase() + name[pos + 1:]
        return name
    return random.choice(variants)


def messy_name_license(emp):
    """License file uses LAST, FIRST but occasionally different."""
    return f"{emp['last'].upper()}, {emp['first'].upper()}"


def messy_phone(phone):
    """Random phone formatting."""
    formats = [
        f"({phone[:3]}) {phone[3:6]}-{phone[6:]}",
        f"{phone[:3]}-{phone[3:6]}-{phone[6:]}",
        f"{phone[:3]}.{phone[3:6]}.{phone[6:]}",
        phone,
    ]
    return random.choice(formats)


def messy_facility_payroll(emp):
    """Payroll uses abbreviations."""
    return emp["fac_code"]


def messy_date(iso_date):
    """Random date format."""
    parts = iso_date.split("-")
    formats = [
        iso_date,
        f"{parts[1]}/{parts[2]}/{parts[0]}",
        f"{parts[1]}-{parts[2]}-{parts[0]}",
    ]
    return random.choice(formats)


# ---------------------------------------------------------------------------
# Generate HR CSV
# ---------------------------------------------------------------------------

def generate_hr_csv():
    path = os.path.join(OUTPUT_DIR, "hr_roster.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "employee_id", "first_name", "last_name", "job_title",
            "facility", "phone", "license_number", "license_expiration", "hire_date"
        ])
        for emp in EMPLOYEES:
            first, last = messy_name_hr(emp)
            writer.writerow([
                emp["id"], first, last, emp["title"],
                emp["facility"], messy_phone(emp["phone"]),
                emp["license"], messy_date(emp["lic_exp"]),
                messy_date(emp["hire"]),
            ])
    print(f"  ✓ {path}")


# ---------------------------------------------------------------------------
# Generate Payroll CSV (multiple pay periods)
# ---------------------------------------------------------------------------

def generate_payroll_csv():
    path = os.path.join(OUTPUT_DIR, "payroll.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "payroll_id", "employee_name", "job_code", "facility_code",
            "period_start", "period_end", "hours_paid"
        ])
        pay_id = 1001
        # Two pay periods
        periods = [
            ("2026-09-14", "2026-09-20"),
            ("2026-09-28", "2026-10-04"),  # matches schedule week
        ]
        for start, end in periods:
            for emp in EMPLOYEES:
                # Intentional: payroll hours sometimes differ from schedule
                base_hours = random.choice([32, 36, 40])
                # Introduce discrepancy for some employees
                if random.random() < 0.3:
                    base_hours += random.choice([-4, 4, -8])

                writer.writerow([
                    f"P{pay_id}",
                    messy_name_payroll(emp),
                    emp["code"],
                    messy_facility_payroll(emp),
                    start, end,
                    base_hours,
                ])
                pay_id += 1
    print(f"  ✓ {path}")


# ---------------------------------------------------------------------------
# Generate Licenses CSV
# ---------------------------------------------------------------------------

def generate_licenses_csv():
    path = os.path.join(OUTPUT_DIR, "licenses.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "license_number", "name_on_license", "license_type",
            "expiration_date", "last_verified"
        ])
        for emp in EMPLOYEES:
            # Occasionally use a slightly different license expiration (STALE data)
            exp = emp["lic_exp"]
            if random.random() < 0.15:
                # Shift by a few days to create a mismatch with HR
                d = date.fromisoformat(exp) + timedelta(days=random.choice([-5, 5, 10]))
                exp = d.isoformat()

            verified = (date.today() - timedelta(days=random.randint(10, 180))).isoformat()

            writer.writerow([
                emp["license"],
                messy_name_license(emp),
                emp["lic_type"],
                messy_date(exp),
                messy_date(verified),
            ])
    print(f"  ✓ {path}")


# ---------------------------------------------------------------------------
# Generate Schedule PDF (using reportlab for a simple table)
# ---------------------------------------------------------------------------

def generate_schedule_pdf():
    """Create a simple PDF schedule matching the brief's format:
    Staff | Role | Mon 09/28 | Tue 09/29 | ...
    One page per facility, titled 'Harborview Bayside' etc.
    """
    try:
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        print("  ⚠ reportlab not installed — skipping PDF generation.")
        print("    Install with: pip install reportlab")
        _generate_schedule_csv_fallback()
        return

    path = os.path.join(OUTPUT_DIR, "weekly_schedule.pdf")
    doc = SimpleDocTemplate(path, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elements = []

    # Match payroll period: 2026-09-28 to 2026-10-04
    day_headers = [
        "Mon 09/28", "Tue 09/29", "Wed 09/30",
        "Thu 10/01", "Fri 10/02", "Sat 10/03", "Sun 10/04",
    ]
    shifts = ["7a-3p", "3p-11p", "11p-7a", "7a-7p"]

    for facility in ["Bayside", "Riverdale"]:
        fac_emps = [e for e in EMPLOYEES if e["facility"] == facility]

        elements.append(Paragraph(
            f"<b>Harborview {facility} — Weekly Schedule (09/28 – 10/04, 2026)</b>",
            styles["Title"]
        ))
        elements.append(Spacer(1, 12))

        header = ["Staff", "Role"] + day_headers
        data = [header]

        for emp in fac_emps:
            row = [f"{emp['first']} {emp['last']}", emp["code"]]
            for _ in day_headers:
                if random.random() < 0.15:
                    row.append("OFF")
                else:
                    row.append(random.choice(shifts))
            data.append(row)

        table = Table(data)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#ecf0f1")]),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 8))

        elements.append(Paragraph(
            "<i>Shifts: 7a-3p, 3p-11p, 11p-7a are 8 hours. 7a-7p is 12 hours.</i>",
            styles["Normal"]
        ))
        elements.append(PageBreak())

    doc.build(elements)
    print(f"  ✓ {path}")


def _generate_schedule_csv_fallback():
    """Fallback: generate schedule as CSV if reportlab is unavailable."""
    path = os.path.join(OUTPUT_DIR, "weekly_schedule.csv")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    shifts = ["7a-3p", "3p-11p", "11p-7a", "7a-7p"]

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Facility", "Employee"] + days)
        for facility in ["Bayside", "Riverdale"]:
            fac_emps = [e for e in EMPLOYEES if e["facility"] == facility]
            for emp in fac_emps:
                row = [facility, f"{emp['first']} {emp['last']}"]
                for _ in days:
                    if random.random() < 0.15:
                        row.append("OFF")
                    else:
                        row.append(random.choice(shifts))
                writer.writerow(row)
    print(f"  ✓ {path} (CSV fallback — no reportlab)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    random.seed(42)  # reproducible messiness
    print("Generating synthetic test data...")
    generate_hr_csv()
    generate_payroll_csv()
    generate_licenses_csv()
    generate_schedule_pdf()
    print("\nDone! Files are in:", OUTPUT_DIR)
