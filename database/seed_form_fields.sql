-- ============================================================
-- EyeVen Canonical Form Field Definitions
-- Seeds eyeven_form_field_values with the 32 target fields
-- from the EyeVen Procurement Vendor Registration Form.
--
-- This is run per-case at case creation time, not globally.
-- The backend service should INSERT these rows when a new
-- vendor_case is created, with status='missing' and
-- value_text=NULL.
--
-- This file serves as the canonical reference for field
-- names, labels, sections, and required flags.
-- ============================================================

USE eyeven;

-- -----------------------------------------------------------
-- Canonical field definitions (reference only — not a table)
--
-- Format:
--   section_name | field_name | field_label | required
-- -----------------------------------------------------------

-- Registration Type
-- new_registration      | new_registration        | Registration Type (New Registration)              | Y
-- re_registration       | re_registration         | Registration Type (Re-registration)               | Y
-- current_vendor_number | current_vendor_number   | Current Vendor Number (if re-registration)        | N

-- Section A. Company General Information
-- company_registered_name      | company_registered_name      | Company Registered Name         | Y
-- company_registered_number    | company_registered_number    | Company Registered Number       | Y
-- company_registered_address   | company_registered_address   | Company Registered Address      | Y
-- company_website              | company_website              | Company Website                 | N
-- company_email                | company_email                | Company Email                   | Y
-- main_office_phone_number     | main_office_phone_number     | Main Office Phone Number        | Y
-- year_of_establishment        | year_of_establishment        | Year of Establishment           | Y
-- business_type                | business_type                | Business Type                   | Y
-- business_type_other          | business_type_other          | Business Type (Other)           | N

-- Section B. Primary Contact Person
-- primary_contact | contact_person_name      | Contact Person Name       | Y
-- primary_contact | designation              | Designation               | Y
-- primary_contact | contact_email            | Contact Email             | Y
-- primary_contact | contact_mobile_number    | Contact Mobile Number     | Y

-- Section C. Financial / Payment Information
-- financial | bank_name                  | Bank Name                 | Y
-- financial | bank_account_name          | Bank Account Name         | Y
-- financial | bank_account_number        | Bank Account Number       | Y
-- financial | latest_annual_revenue_range| Latest Annual Revenue Range| N

-- Section D. Registration / Certification
-- registration | ssm_roc_rob_number          | SSM / ROC / ROB Number           | Y
-- registration | tax_sst_gst_number          | Tax / SST / GST Number           | Y
-- registration | iso_certification_available | ISO Certification Available      | Y
-- registration | iso_certification_number    | ISO Certification Number         | N
-- registration | iso_certification_expiry_date| ISO Certification Expiry Date   | N

-- Section E. Business & Risk Declaration
-- risk_declaration | main_business_activities   | Main Business Activities            | Y
-- risk_declaration | top_3_major_clients        | Top 3 Major Clients                 | N
-- risk_declaration | legal_disputes_past_5_years| Legal Disputes Past 5 Years        | Y
-- risk_declaration | legal_disputes_details     | Legal Disputes Details              | N
-- risk_declaration | outsource_any_part_of_services | Outsource Any Part of Services | Y
-- risk_declaration | outsourcing_details        | Outsourcing Details                 | N
-- risk_declaration | number_of_employees        | Number of Employees                 | Y

-- Supplier Declaration
-- supplier_declaration | authorized_signature_present   | Authorized Signature Present   | Y
-- supplier_declaration | signatory_name                 | Signatory Name                 | Y
-- supplier_declaration | signatory_designation          | Signatory Designation          | Y
-- supplier_declaration | signatory_ic_or_passport_number| Signatory IC / Passport Number | Y
-- supplier_declaration | declaration_date               | Declaration Date               | Y
-- supplier_declaration | company_chop_present           | Company Chop Present           | Y


-- ============================================================
-- Stored procedure: seed canonical fields for a new case
-- Call: CALL seed_eyeven_form_fields(@new_case_id);
-- ============================================================

DELIMITER //

DROP PROCEDURE IF EXISTS seed_eyeven_form_fields //

CREATE PROCEDURE seed_eyeven_form_fields(IN p_vendor_case_id BIGINT)
BEGIN
    -- Registration Type
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'registration_type', 'new_registration',       'Registration Type (New Registration)',          'unknown', 'missing'),
        (p_vendor_case_id, 'registration_type', 're_registration',        'Registration Type (Re-registration)',           'unknown', 'missing'),
        (p_vendor_case_id, 'registration_type', 'current_vendor_number',  'Current Vendor Number (if re-registration)',    'unknown', 'missing');

    -- Section A. Company General Information
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'company_general', 'company_registered_name',    'Company Registered Name',    'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'company_registered_number',  'Company Registered Number',  'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'company_registered_address', 'Company Registered Address', 'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'company_website',            'Company Website',            'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'company_email',              'Company Email',              'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'main_office_phone_number',   'Main Office Phone Number',   'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'year_of_establishment',      'Year of Establishment',      'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'business_type',              'Business Type',              'unknown', 'missing'),
        (p_vendor_case_id, 'company_general', 'business_type_other',        'Business Type (Other)',      'unknown', 'missing');

    -- Section B. Primary Contact Person
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'primary_contact', 'contact_person_name',   'Contact Person Name',   'unknown', 'missing'),
        (p_vendor_case_id, 'primary_contact', 'designation',           'Designation',           'unknown', 'missing'),
        (p_vendor_case_id, 'primary_contact', 'contact_email',         'Contact Email',         'unknown', 'missing'),
        (p_vendor_case_id, 'primary_contact', 'contact_mobile_number', 'Contact Mobile Number', 'unknown', 'missing');

    -- Section C. Financial / Payment Information
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'financial', 'bank_name',                   'Bank Name',                   'unknown', 'missing'),
        (p_vendor_case_id, 'financial', 'bank_account_name',           'Bank Account Name',           'unknown', 'missing'),
        (p_vendor_case_id, 'financial', 'bank_account_number',         'Bank Account Number',         'unknown', 'missing'),
        (p_vendor_case_id, 'financial', 'latest_annual_revenue_range', 'Latest Annual Revenue Range', 'unknown', 'missing');

    -- Section D. Registration / Certification
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'registration', 'ssm_roc_rob_number',           'SSM / ROC / ROB Number',          'unknown', 'missing'),
        (p_vendor_case_id, 'registration', 'tax_sst_gst_number',           'Tax / SST / GST Number',          'unknown', 'missing'),
        (p_vendor_case_id, 'registration', 'iso_certification_available',  'ISO Certification Available',     'unknown', 'missing'),
        (p_vendor_case_id, 'registration', 'iso_certification_number',     'ISO Certification Number',        'unknown', 'missing'),
        (p_vendor_case_id, 'registration', 'iso_certification_expiry_date','ISO Certification Expiry Date',   'unknown', 'missing');

    -- Section E. Business & Risk Declaration
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'risk_declaration', 'main_business_activities',       'Main Business Activities',            'unknown', 'missing'),
        (p_vendor_case_id, 'risk_declaration', 'top_3_major_clients',            'Top 3 Major Clients',                 'unknown', 'missing'),
        (p_vendor_case_id, 'risk_declaration', 'legal_disputes_past_5_years',    'Legal Disputes Past 5 Years',        'unknown', 'missing'),
        (p_vendor_case_id, 'risk_declaration', 'legal_disputes_details',         'Legal Disputes Details',             'unknown', 'missing'),
        (p_vendor_case_id, 'risk_declaration', 'outsource_any_part_of_services', 'Outsource Any Part of Services',     'unknown', 'missing'),
        (p_vendor_case_id, 'risk_declaration', 'outsourcing_details',            'Outsourcing Details',                'unknown', 'missing'),
        (p_vendor_case_id, 'risk_declaration', 'number_of_employees',            'Number of Employees',                'unknown', 'missing');

    -- Supplier Declaration
    INSERT INTO eyeven_form_field_values (vendor_case_id, section_name, field_name, field_label, value_source, status) VALUES
        (p_vendor_case_id, 'supplier_declaration', 'authorized_signature_present',    'Authorized Signature Present',    'unknown', 'missing'),
        (p_vendor_case_id, 'supplier_declaration', 'signatory_name',                  'Signatory Name',                  'unknown', 'missing'),
        (p_vendor_case_id, 'supplier_declaration', 'signatory_designation',           'Signatory Designation',           'unknown', 'missing'),
        (p_vendor_case_id, 'supplier_declaration', 'signatory_ic_or_passport_number', 'Signatory IC / Passport Number',  'unknown', 'missing'),
        (p_vendor_case_id, 'supplier_declaration', 'declaration_date',                'Declaration Date',                'unknown', 'missing'),
        (p_vendor_case_id, 'supplier_declaration', 'company_chop_present',            'Company Chop Present',            'unknown', 'missing');
END //

DELIMITER ;
