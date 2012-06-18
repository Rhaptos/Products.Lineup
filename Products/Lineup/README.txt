= Lineup =

Simple generic queue system for Zope/Plone for allowing applications to handle
asynchronous tasks.

author: J Cameron Cooper (jccooper@rice.edu) and Brian N West (bnwest@rice.edu)
Started 23 July 2009
Copyright (c) 2009, Rice University. All rights reserved.

This Zope/Plone Product is used as part of the Rhaptos system (http://rhaptos.org),
created to run Connexions (http://cnx.org.)

Install through GenericSetup, or through standard QuickInstaller, which runs GenericSetup,
in any portal after installing the code in the standard (non-egg/buildout) manner for Products:
that is, put the directory in /Products. QuickInstaller is most useful in Rhaptos context,
because we cannot yet run all steps; QI runs only seelcted steps that are needed for this product.

Subsequently, you will need to register queue event handlers and place event calls in
your code to trigger them; this product is not useful without some coding.

XXX: REMAINDER TODO