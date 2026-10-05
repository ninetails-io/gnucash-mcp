;; Creates a brand-new SQLite book through GnuCash's own ENGINE, so the
;; suite has a book whose tables GnuCash made (the server's other
;; fixtures are piecash-made, and their definitions differ in small
;; ways: adversarial review 2026-09-30, FC-19). Driven by
;; tests/fixtures/gnucash_made.py through gnucash-cli; nothing here is
;; installed anywhere, and the book it makes is never committed.
;;
;; $NEW_BOOK names the file to create. The chart is the suite's usual
;; one: Assets:Checking, Assets:Accounts Receivable,
;; Liabilities:Accounts Payable, Income:Sales, Expenses:Groceries,
;; Equity:Opening Balances, all USD, with USD on the root account as
;; the New Account Hierarchy assistant leaves it.
;;
;; GnuCash's Guile bindings wrap qof_session_new but not
;; qof_session_begin or qof_session_save, so the engine's C functions
;; are called through Guile's foreign-function interface. gnucash-cli
;; needs SOME book to run a report against; the new one is separate.
(define-module (gnucash reports new-book))
(use-modules (gnucash engine))
(use-modules (gnucash utilities))
(use-modules (gnucash core-utils))
(use-modules (gnucash app-utils))
(use-modules (gnucash report))
(use-modules (gnucash html))
(use-modules (system foreign))
(define (options-generator) (gnc:new-options))
(define lib (dynamic-link "libgnc-engine"))
(define (c ret name . args)
  (pointer->procedure ret (dynamic-func name lib) args))
(define (make! path)
  (let* ((book ((c '* "qof_book_new")))
         (session ((c '* "qof_session_new" '*) book))
         (url (string->pointer (string-append "sqlite3://" path))))
    ((c void "qof_session_begin" '* '* int) session url 2)
    (let* ((book ((c '* "qof_session_get_book" '*) session))
           (root ((c '* "gnc_book_get_root_account" '*) book))
           (table ((c '* "gnc_commodity_table_get_table" '*) book))
           (usd ((c '* "gnc_commodity_table_lookup" '* '* '*)
                 table (string->pointer "CURRENCY") (string->pointer "USD")))
           (add (lambda (parent name type placeholder)
                  (let ((a ((c '* "xaccMallocAccount" '*) book)))
                    ((c void "xaccAccountBeginEdit" '*) a)
                    ((c void "xaccAccountSetName" '* '*) a (string->pointer name))
                    ((c void "xaccAccountSetType" '* int) a type)
                    ((c void "xaccAccountSetCommodity" '* '*) a usd)
                    ((c void "xaccAccountSetPlaceholder" '* int) a placeholder)
                    ((c void "gnc_account_append_child" '* '*) parent a)
                    ((c void "xaccAccountCommitEdit" '*) a)
                    a))))
      ((c void "xaccAccountBeginEdit" '*) root)
      ((c void "xaccAccountSetCommodity" '* '*) root usd)
      ((c void "xaccAccountCommitEdit" '*) root)
      (let ((assets (add root "Assets" 2 1))
            (liab (add root "Liabilities" 4 1))
            (income (add root "Income" 8 1))
            (expenses (add root "Expenses" 9 1))
            (equity (add root "Equity" 10 1)))
        (add assets "Checking" 0 0)
        (add assets "Accounts Receivable" 11 0)
        (add liab "Accounts Payable" 12 0)
        (add income "Sales" 8 0)
        (add expenses "Groceries" 9 0)
        (add equity "Opening Balances" 10 0))
      ((c void "qof_session_save" '* '*) session %null-pointer)
      (let ((err ((c int "qof_session_get_error" '*) session)))
        ((c void "qof_session_end" '*) session)
        (number->string err)))))
(define (renderer report-obj)
  (let ((doc (gnc:make-html-document)))
    (gnc:html-document-add-object!
     doc (gnc:make-html-text
          (gnc:html-markup-p (string-append "NEW|" (make! (getenv "NEW_BOOK")) "|"))))
    doc))
(gnc:define-report 'version 1 'name "New Book"
 'report-guid "2b3c4d5e6f708192a3b4c5d6e7f8091a"
 'menu-path (list gnc:menuname-example)
 'options-generator options-generator 'renderer renderer)
