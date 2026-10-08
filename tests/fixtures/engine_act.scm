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
;;   price|<namespace>|<mnemonic>|<currency mnemonic>|<d>|<m>|<y>|<value>|<source>|<type>
;;       gnc_pricedb_add_price — what the Price Editor's OK button and
;;       a Finance::Quote fetch both call. Result "ok" when the price
;;       went in, "rejected" when the database kept what it had.
;;   post|<invoice guid>|<post account guid>|<d>|<m>|<y>|<due d>|<due m>|<due y>|<memo>
;;       gncInvoicePostToAccount with splits accumulated per account
;;       and no auto-pay — the Post dialog's defaults.
;;   balance|<customer guid>
;;       gncOwnerGetBalanceInCurrency, in the customer's own currency —
;;       the figure behind the Customers Overview balance column.
;;       Changes nothing; the result is "balance=<number>".
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
     ((string=? verb "post")
      (let ((inv (gncInvoiceLookupFlip (list-ref fields 1) book))
            (acc (xaccAccountLookup (list-ref fields 2) book))
            (n (lambda (i) (string->number (list-ref fields i)))))
        (cond ((or (null? inv) (null? acc)) "missing")
              ((null? (gncInvoicePostToAccount
                       inv acc
                       (gnc-dmy2time64-neutral (n 3) (n 4) (n 5))
                       (gnc-dmy2time64-neutral (n 6) (n 7) (n 8))
                       (list-ref fields 9) #t #f))
               "refused")
              (else "ok"))))
     ((string=? verb "balance")
      (let ((customer (gncCustomerLookupFlip (list-ref fields 1) book))
            (owner (gncOwnerNew)))
        (if (null? customer)
            "missing"
            (begin
              (gncOwnerInitCustomer owner customer)
              (string-append
               "balance="
               (number->string (gncOwnerGetBalanceInCurrency owner '())))))))
     ((string=? verb "price")
      (let* ((table (gnc-commodity-table-get-table book))
             (commodity (gnc-commodity-table-lookup
                         table (list-ref fields 1) (list-ref fields 2)))
             (currency (gnc-commodity-table-lookup
                        table "CURRENCY" (list-ref fields 3)))
             (date (gnc-dmy2time64-neutral
                    (string->number (list-ref fields 4))
                    (string->number (list-ref fields 5))
                    (string->number (list-ref fields 6)))))
        (if (or (null? commodity) (null? currency))
            "missing"
            (let ((price (gnc-price-create book)))
              (gnc-price-begin-edit price)
              (gnc-price-set-commodity price commodity)
              (gnc-price-set-currency price currency)
              (gnc-price-set-time64 price date)
              (gnc-price-set-source-string price (list-ref fields 8))
              (gnc-price-set-typestr price (list-ref fields 9))
              (gnc-price-set-value price (string->number (list-ref fields 7)))
              (gnc-price-commit-edit price)
              (let ((added (gnc-pricedb-add-price
                            (gnc-pricedb-get-db book) price)))
                (gnc-price-unref price)
                (if added "ok" "rejected"))))))
     ;; txn|path|from|to|amount|d|m|y|description|num|link
     ;; path "import": desktop's CSV importer (xaccTransSetNum);
     ;; path "register": the register's Num cell on the FROM
     ;; account's split (gnc_set_num_action, engine-helpers.c).
     ((string=? verb "txn")
      (let ((from (xaccAccountLookup (list-ref fields 2) book))
            (to (xaccAccountLookup (list-ref fields 3) book))
            (amount (string->number (list-ref fields 4)))
            (n (lambda (i) (string->number (list-ref fields i))))
            (num (list-ref fields 9))
            (link (list-ref fields 10)))
        (if (or (null? from) (null? to))
            "missing"
            (let ((trans (xaccMallocTransaction book))
                  (s1 (xaccMallocSplit book))
                  (s2 (xaccMallocSplit book)))
              (xaccTransBeginEdit trans)
              (xaccTransSetCurrency trans (xaccAccountGetCommodity from))
              (xaccTransSetDatePostedSecsNormalized
               trans (gnc-dmy2time64-neutral (n 5) (n 6) (n 7)))
              (xaccTransSetDescription trans (list-ref fields 8))
              (xaccSplitSetParent s1 trans)
              (xaccSplitSetAccount s1 from)
              (xaccSplitSetValue s1 (- amount))
              (xaccSplitSetAmount s1 (- amount))
              (xaccSplitSetParent s2 trans)
              (xaccSplitSetAccount s2 to)
              (xaccSplitSetValue s2 amount)
              (xaccSplitSetAmount s2 amount)
              (if (string=? (list-ref fields 1) "register")
                  (gnc-set-num-action trans s1 num #f)
                  (xaccTransSetNum trans num))
              (if (not (string-null? link))
                  (xaccTransSetDocLink trans link))
              (xaccTransCommitEdit trans)
              "ok"))))
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
