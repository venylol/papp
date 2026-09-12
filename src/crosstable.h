/* crosstable.h
 *
 *  - prototypes pour crosstable.c
 *  - prototypes for crosstable.c
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 */

#ifndef __Crosstable_h__
#define __Crosstable_h__

long TextCrosstableSave(long whichRound, char *textFilename, char *openingMode);
long HTMLCrosstableSave(char *htmlFilename);

void PrepareCrosstableCalculations(long whichRound);
void DisplayCrosstable(void);
long CanAllocateCrosstableMemory(void);
void FreeCrosstableMemory(void);


#endif  /*  #ifndef __Crosstable_h__ */
