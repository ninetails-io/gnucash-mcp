;; A GnuCash report that prints the ENGINE's own totals for the
;; documents whose GUIDs are listed in the file named by $PARITY_GUIDS
;; (gncInvoiceGetTotal / …TotalSubtotal / …TotalTax). One line each:
;;   PARITY|<guid>|<total>|<subtotal>|<tax>     (exact rationals)
;;
;; The desktop half of the arithmetic parity oracle; driven headlessly
;; by tests/fixtures/desktop_totals.py through gnucash-cli. Nothing
;; here is installed anywhere: the driver points GNC_DATA_HOME and
;; GNC_CONFIG_HOME at a temp directory for the one run.
(define-module (gnucash reports parity-totals))
(use-modules (gnucash engine))
(use-modules (gnucash utilities))
(use-modules (gnucash core-utils))
(use-modules (gnucash app-utils))
(use-modules (gnucash report))
(use-modules (gnucash html))
(use-modules (ice-9 rdelim))

(define (options-generator) (gnc:new-options))

(define (read-guids path)
  (call-with-input-file path
    (lambda (port)
      (let loop ((acc '()) (line (read-line port)))
        (if (eof-object? line)
            (reverse acc)
            (loop (if (string-null? line) acc (cons line acc))
                  (read-line port)))))))

(define (renderer report-obj)
  (let* ((doc (gnc:make-html-document))
         (book (gnc-get-current-book))
         (guids (read-guids (getenv "PARITY_GUIDS"))))
    (for-each
     (lambda (g)
       (let ((inv (gncInvoiceLookupFlip g book)))
         (gnc:html-document-add-object!
          doc
          (gnc:make-html-text
           (gnc:html-markup-p
            (string-append
             "PARITY|" g "|"
             (if (null? inv)
                 "MISSING"
                 (string-append
                  (number->string (gncInvoiceGetTotal inv)) "|"
                  (number->string (gncInvoiceGetTotalSubtotal inv)) "|"
                  (number->string (gncInvoiceGetTotalTax inv))))))))))
     guids)
    doc))

(gnc:define-report
 'version 1
 'name "Parity Totals"
 'report-guid "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
 'menu-path (list gnc:menuname-example)
 'options-generator options-generator
 'renderer renderer)
