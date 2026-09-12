/* pairings.h
 *
 * - entete pour l'algorithme d'appariement
 * - header for the pairing algorithm
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33 All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#ifndef __Pairings_h__
#define __Pairings_h__

/* #if defined(UNIX_BSD) || defined(UNIX_SYSV) added */
/* change by Stephane Nicolet , 17 march 2000 */
#if defined(UNIX_BSD) || defined(UNIX_SYSV)
    #include <sys/types.h>
#endif

#include <limits.h>

typedef signed long pen_t;
#define MAX_PEN         10000000L

pen_t doPairings(long n, pen_t **penalites, long *solution);

#endif  /* #ifndef __Pairings_h__ */
