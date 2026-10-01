;; Runs a script of business actions through GnuCash's own ENGINE and
;; lets its SQL backend write them — the desktop half of a parity twin
;; with no desktop on screen. Driven by tests/fixtures/engine_twin.py
;; through gnucash-cli; nothing here is installed anywhere.
;;
;; $PARITY_ACTIONS names a file, one action per line, fields split on "|":
;;   pay|<invoice guid>|<transfer account guid>|<amount>|<exch>|<d>|<m>|<y>|<memo>|<num>
;;       gncInvoiceApplyPayment — what the Process Payment dialog calls
;;       for a payment made against one document.
;;   unpost|<invoice guid>
;;       gncInvoiceUnpost (tax tables left as they are).
;;   autoapply|<invoice guid>
;;       gncInvoiceAutoApplyPayments — the "auto pay when posting" pass.
;;
;; gnucash-cli opens a report's book with SESSION_READ_ONLY, which for
;; the SQL backend only skips the lock: every commit_edit still writes.
(define-module (gnucash reports engine-act))
(use-modules (gnucash engine))
(use-modules (gnucash utilities))
(use-modules (gnucash core-utils))
(use-modules (gnucash app-utils))
(use-modules (gnucash report))
(use-modules (gnucash html))
(use-modules (ice-9 rdelim))

(define (options-generator) (gnc:new-options))

(define (read-lines path)
  (call-with-input-file path
    (lambda (port)
      (let loop ((acc '()) (line (read-line port)))
        (if (eof-object? line)
            (reverse acc)
            (loop (if (string-null? line) acc (cons line acc))
                  (read-line port)))))))

(define (act! book fields)
  (let ((verb (car fields)))
    (cond
     ((string=? verb "pay")
      (let* ((inv (gncInvoiceLookupFlip (list-ref fields 1) book))
             (xfer (xaccAccountLookup (list-ref fields 2) book))
             (amount (string->number (list-ref fields 3)))
             (exch (string->number (list-ref fields 4)))
             (date (gnc-dmy2time64-neutral
                    (string->number (list-ref fields 5))
                    (string->number (list-ref fields 6))
                    (string->number (list-ref fields 7)))))
        (if (or (null? inv) (null? xfer))
            "missing"
            (begin
              (gncInvoiceApplyPayment inv '() xfer amount exch date
                                      (list-ref fields 8) (list-ref fields 9))
              "ok"))))
     ((string=? verb "unpost")
      (let ((inv (gncInvoiceLookupFlip (list-ref fields 1) book)))
        (cond ((null? inv) "missing")
              ((gncInvoiceUnpost inv #f) "ok")
              (else "refused"))))
     ((string=? verb "autoapply")
      (let ((inv (gncInvoiceLookupFlip (list-ref fields 1) book)))
        (if (null? inv)
            "missing"
            (begin (gncInvoiceAutoApplyPayments inv) "ok"))))
     (else "unknown"))))

(define (renderer report-obj)
  (let* ((doc (gnc:make-html-document))
         (book (gnc-get-current-book)))
    (for-each
     (lambda (line)
       (let ((result (act! book (string-split line #\|))))
         (gnc:html-document-add-object!
          doc (gnc:make-html-text
               (gnc:html-markup-p (string-append "ACT|" result "|" line))))))
     (read-lines (getenv "PARITY_ACTIONS")))
    doc))

(gnc:define-report
 'version 1
 'name "Engine Act"
 'report-guid "1a2b3c4d5e6f708192a3b4c5d6e7f809"
 'menu-path (list gnc:menuname-example)
 'options-generator options-generator
 'renderer renderer)
