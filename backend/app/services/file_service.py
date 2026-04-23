class FileService:
    @staticmethod
    def upload_files(case_id, files, email_content=None):
        # TODO: validate, store, classify, trigger extraction
        return {"case_id": case_id, "status": "upload_not_yet_implemented"}

    @staticmethod
    def upload_corrected_files(case_id, files):
        # TODO: validate, store as corrected_upload, trigger reprocessing
        return {"case_id": case_id, "status": "corrected_upload_not_yet_implemented"}
