#!/usr/bin/env python3
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
#       Will Thompson <wjt@endlessm.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import logging
import os
import unittest
import sys
from io import StringIO

# Yuck!
tests_dir = os.path.dirname(__file__)
checker_path = os.path.join(tests_dir, "..", "src")
sys.path.append(checker_path)

from lib.appdata import add_release  # noqa: E402


class TestAddRelease(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.DEBUG)

    def _do_test(self, before, expected):
        in_ = StringIO(before)
        out = StringIO()
        add_release(in_, out, "4.5.6", "2020-02-02")
        self.assertMultiLineEqual(out.getvalue(), expected)

    def test_simple(self):
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="1.2.3" date="2019-01-01"/>
  </releases>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
    <release version="1.2.3" date="2019-01-01"/>
  </releases>
</component>
            """.strip(),
        )

    def test_mixed_indentation(self):
        """This input uses 3-space indentation for one existing <release> and 4-space
        for another. Match the top one."""
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
   <releases>
      <release version="1.2.3" date="2019-01-01"/>
       <release version="1.2.3" date="2019-01-01"/>
   </releases>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
   <releases>
      <release version="4.5.6" date="2020-02-02"/>
      <release version="1.2.3" date="2019-01-01"/>
       <release version="1.2.3" date="2019-01-01"/>
   </releases>
</component>
            """.strip(),
        )

    @unittest.expectedFailure
    def test_release_attribute_ordering(self):
        """It would be nice to follow the attribute order on any existing <release>s.
        Currently we always emit version then date. I checked 18 repos and it was a
        10-8 split."""
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release date="2019-01-01" version="1.2.3"/>
  </releases>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release date="2020-02-02" version="4.5.6"/>
    <release date="2019-01-01" version="1.2.3"/>
  </releases>
</component>
            """.strip(),
        )

    def test_comment(self):
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <!-- I am the walrus -->
  <releases>
    <release version="1.2.3" date="2019-01-01"/>
  </releases>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <!-- I am the walrus -->
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
    <release version="1.2.3" date="2019-01-01"/>
  </releases>
</component>
            """.strip(),
        )

    def test_no_releases(self):
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
  </releases>
</component>
            """.strip(),
        )

    def test_empty_releases(self):
        """No whitespace is generated between <release /> and </releases>."""
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases/>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
  </releases>
</component>
            """.strip(),
            # but we can live with it in this edge case for now
        )

    def test_double_comment_within_root(self):
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<application>
<!-- Copyright 2019 Rupert Monkey <rupert@gnome.org> -->
 <!--
EmailAddress: billg@example.com
SentUpstream: 2014-05-22
-->
</application>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<application>
<!-- Copyright 2019 Rupert Monkey <rupert@gnome.org> -->
 <!--
EmailAddress: billg@example.com
SentUpstream: 2014-05-22
-->
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
  </releases>
</application>
            """.strip(),
        )

    def test_comment_outside_root(self):
        # appdata files often include a copyright comment between the <?xml
        # declaration and the root element. In some cases, there are two.
        # Unfortunately, there is no representation in the SAX API for
        # whitespace that occurs outside any element. Test that the
        # conventional newline after such comments is preserved.

        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<!-- Copyright 2019 Rupert Monkey <rupert@gnome.org> -->
<!--
EmailAddress: billg@example.com
SentUpstream: 2014-05-22
-->
<application>
</application>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<!-- Copyright 2019 Rupert Monkey <rupert@gnome.org> -->
<!--
EmailAddress: billg@example.com
SentUpstream: 2014-05-22
-->
<application>
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
  </releases>
</application>
            """.strip(),
        )

    def test_amp_as_amp(self):
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <name>🍦 &amp; 🎂</name>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <name>🍦 &amp; 🎂</name>
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
  </releases>
</component>
            """.strip(),
        )

    @unittest.expectedFailure
    def test_amp_as_codepoint(self):
        """&#38; becomes &amp;."""
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <name>🦝 &#38; 🍒</name>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <name>🦝 &#38; 🍒</name>
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
  </releases>
</component>
            """.strip(),
        )

    def test_downgrade_already_present(self):
        # If the update is a downgrade, we cannot simply prepend the new release to the
        # list, or the appdata will be invalid.
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.7" date="2021-03-03">
      <description>
        <p>Another lovely release.</p>
      </description>
    </release>
    <release version="4.5.6" date="2020-02-02">
      <description>
        <p>What a lovely release.</p>
      </description>
    </release>
  </releases>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.6" date="2020-02-02">
      <description>
        <p>What a lovely release.</p>
      </description>
    </release>
  </releases>
</component>
            """.strip(),
        )

    def test_downgrade_not_present(self):
        # As above, this is a downgrade, but to a version that was not already in the
        # appdata.
        self._do_test(
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.7" date="2021-03-03">
      <description>
        <p>Another lovely release.</p>
      </description>
    </release>
    <release version="1.2.3" date="2019-01-01">
      <description>
        <p>What an ancient release.</p>
      </description>
    </release>
  </releases>
</component>
            """.strip(),
            """
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <releases>
    <release version="4.5.6" date="2020-02-02"/>
    <release version="1.2.3" date="2019-01-01">
      <description>
        <p>What an ancient release.</p>
      </description>
    </release>
  </releases>
</component>
            """.strip(),
        )


if __name__ == "__main__":
    unittest.main()
